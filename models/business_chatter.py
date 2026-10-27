"""Seguimiento adicional de documentos operativos sin sustituir sus mensajes."""
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

from markupsafe import Markup
from odoo import api, models
from odoo.tools import html2plaintext, ormcache

from .material_chatter import (
    LINE_CONFIG, TECHNICAL_FIELDS, TECHNICAL_PREFIXES, chatter_language,
    enabled, extra_tracking, field_label,
)
from .product_audit import ORIGIN as PRODUCT_ORIGIN


OPERATION = ContextVar('entintados_business_operation', default=None)
POSTING = ContextVar('entintados_business_audit_posting', default=False)
MODULES = {
    'pos.accounting.return': 'Contabilidad',
    'pos.accounting.return.line': 'Contabilidad',
    'sale.order': 'Ventas', 'sale.order.line': 'Ventas',
    'purchase.order': 'Compras', 'purchase.order.line': 'Compras',
    'account.move': 'Contabilidad', 'account.move.line': 'Contabilidad',
    'account.payment': 'Contabilidad', 'stock.picking': 'Inventario',
    'stock.move': 'Inventario', 'stock.move.line': 'Inventario',
    'stock.lot': 'Inventario', 'stock.quant': 'Inventario',
    'account.bank.statement.line': 'Contabilidad', 'account.partial.reconcile': 'Contabilidad',
}
PARENTS = {
    'sale.order.line': 'order_id', 'purchase.order.line': 'order_id',
    'account.move.line': 'move_id', 'stock.move': 'picking_id',
    'stock.move.line': 'picking_id',
    'account.bank.statement.line': 'move_id',
}


def audit_enabled(records):
    return not POSTING.get() and not records.env.context.get('install_mode')


def legacy_covers(records):
    config = LINE_CONFIG.get(records._name)
    return config and enabled(records) and all(record[config[0]] for record in records)


def uncovered_names(record, names, mode='write'):
    names = set(names)
    if legacy_covers(record):
        names -= set(LINE_CONFIG[record._name][1])
    if (record._name == 'account.move.line' and record.move_id.posted_before
            and not record.env.context.get('tracking_disable')):
        names = {n for n in names if not (getattr(record._fields.get(n), 'tracking', False)
                                         and not record._fields[n].related)}
    if mode == 'write' and record._name == 'sale.order.line' and record.state == 'sale' and 'product_id' not in names:
        # La cantidad confirmada ya aparece en el mensaje nativo del pedido.
        names.discard('product_uom_qty')
    if mode == 'write' and record._name == 'purchase.order.line' and record.order_id.state == 'purchase':
        names.discard('product_qty')
        if not record.env.context.get('accrual_entry_date'):
            names.discard('qty_received')
    return names


def document_label(record):
    record.ensure_one()
    return record.display_name or record._description or ('#%s' % record.id)


def metadata(records, action='Edición / API'):
    root = OPERATION.get()
    if root:
        return root
    actor = records.env.user
    origin = PRODUCT_ORIGIN.get()
    if not origin:
        origin = '%s · %s' % (MODULES.get(records._name, records._description), action)
        if records.env.context.get('import_file'):
            origin += ' · Importación'
        if len(records) == 1:
            origin += ' · ' + document_label(records)
    return (origin, actor.name, actor.partner_id.id)


@contextmanager
def operation(records, action):
    token = OPERATION.set(metadata(records, action))
    product_token = PRODUCT_ORIGIN.set(OPERATION.get()[0])
    try:
        yield
    finally:
        PRODUCT_ORIGIN.reset(product_token)
        OPERATION.reset(token)


def provenance_html(info):
    origin, actor, _partner = info
    return Markup('<p class="o_business_audit"><small>Origen: %s · Usuario: %s</small></p>') % (origin, actor)


def enrich_message(record, kwargs):
    if OPERATION.get() and not POSTING.get() and 'o_business_audit' not in str(kwargs.get('body', '')):
        info = metadata(record)
        kwargs['body'] = Markup('').join([kwargs.get('body') or '', provenance_html(info)])
        if not kwargs.get('author_id'):
            kwargs['author_id'] = info[2]
    return kwargs


def parent_for(record):
    name = PARENTS.get(record._name)
    if name and record[name]:
        return record[name]
    if record._name in ('stock.move', 'stock.move.line', 'stock.quant'):
        return record.product_id.product_tmpl_id
    return record if record._name in MODULES and record._name not in PARENTS else record.browse()


def values(record, names):
    result = {}
    localized = record.with_context(lang=chatter_language(record))
    for name in names:
        field = record._fields.get(name)
        if (not field or name in TECHNICAL_FIELDS or name.startswith(TECHNICAL_PREFIXES)
                or field.type == 'one2many' or field.inherited or not record._has_field_access(field, 'read')):
            continue
        value = record[name]
        if field.type in ('many2one', 'many2many'):
            result[name] = (tuple(sorted(value.ids)), ', '.join(value.mapped('display_name')) or '—')
        elif field.type == 'binary':
            raw = record.with_context(bin_size=False)[name] or b''
            raw = raw.encode() if isinstance(raw, str) else raw
            result[name] = (hashlib.sha256(raw).hexdigest(), 'Archivo presente' if raw else 'Sin archivo')
        elif field.type == 'html':
            text = html2plaintext(value or '')
            result[name] = (text, text or '—')
        elif field.type == 'selection':
            result[name] = (value, dict(field._description_selection(localized.env)).get(value, '—'))
        elif field.type == 'boolean':
            result[name] = (value, 'Sí' if value else 'No')
        elif field.type in ('json', 'properties', 'properties_definition'):
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            result[name] = (text, text)
        else:
            result[name] = (value, str(value) if value is not False and value is not None else '—')
    return result


def post(parent, title, changes, info):
    if not parent or not parent.exists() or not changes:
        return
    body = Markup('<p><strong>%s</strong></p>%s<ul>%s</ul>') % (
        title, provenance_html(info),
        Markup('').join(Markup('<li>%s: %s → %s</li>') % change for change in changes))
    token = POSTING.set(True)
    try:
        # Se eleva únicamente la bitácora después de validar la escritura original.
        # Una nota interna no envía correos ni modifica los seguidores.
        parent.sudo()._message_log(body=body, author_id=info[2])
    finally:
        POSTING.reset(token)


def write_audit(records, vals, write, document=False):
    if (not audit_enabled(records) or not any(
            n not in TECHNICAL_FIELDS and not n.startswith(TECHNICAL_PREFIXES) for n in vals)):
        return write(vals)
    info = metadata(records)
    tracked = records._track_get_fields() if document else set()
    native = records.env.cr.precommit.data.get('mail.tracking.' + records._name, {})
    before = {}
    for record in records:
        names = set(vals)
        if document:
            # mail.thread descarta el tracking de registros recién creados en
            # la misma transacción; en ese caso guardar el cambio adicional.
            if enabled(record) and native.get(record.id, ()) is not None:
                names -= tracked
                if set(vals) & tracked:
                    origins = records.env.cr.precommit.data.setdefault('business.audit.' + records._name, {})
                    entry = (info, frozenset(vals))
                    if entry not in origins.setdefault(record.id, []):
                        origins[record.id].append(entry)
            if record._name == 'stock.picking' and enabled(record):
                names = {n for n in names if record._fields.get(n) and record._fields[n].type not in ('html', 'binary')}
        else:
            names = uncovered_names(record, names)
        before[record.id] = (parent_for(record), document_label(record), values(record, names))
    with operation(records, 'Edición / API'):
        result = write(vals)
    for record in records:
        old_parent, label, old = before[record.id]
        new = values(record, old)
        changes = []
        for name, (initial, text) in old.items():
            if initial != new[name][0]:
                current = new[name][1]
                if record._fields[name].type == 'binary' and text == current:
                    current = 'Archivo reemplazado'
                changes.append((field_label(record, name), text, current))
        new_parent = parent_for(record)
        post(old_parent, 'Cambios en %s' % label, changes, info)
        if new_parent != old_parent:
            post(new_parent, 'Registro trasladado: %s' % label,
                 changes or [('Documento anterior', document_label(old_parent) if old_parent else '—', document_label(new_parent))], info)
    return result


def create_audit(records, vals_list, create):
    info = metadata(records, 'Creación')
    with operation(records, 'Creación'):
        created = create(vals_list)
    if audit_enabled(records):
        for record, vals in zip(created, vals_list):
            names = uncovered_names(record, vals, mode='create')
            changes = [(field_label(record, name), '—', label) for name, (_value, label) in values(record, names).items()]
            post(parent_for(record), 'Registro agregado: %s' % document_label(record), changes, info)
    return created


def unlink_audit(records, unlink):
    info = metadata(records, 'Eliminación')
    before = []
    if audit_enabled(records):
        for record in records:
            names = {n for n, f in record._fields.items() if f.store and not f.compute and not f.related}
            names = uncovered_names(record, names, mode='unlink')
            before.append((parent_for(record), document_label(record), values(record, names)))
    with operation(records, 'Eliminación'):
        result = unlink()
    for parent, label, old in before:
        changes = [(field_label(records, name), text, '—') for name, (_value, text) in old.items()]
        post(parent, 'Registro eliminado: %s' % label, changes, info)
    return result


def track_audit(records, fields_iter, initial, track):
    origins = records.env.cr.precommit.data.pop('business.audit.' + records._name, {})
    bodies = records.env.cr.precommit.data.setdefault('mail.tracking.message.' + records._name, {})
    authors = records.env.cr.precommit.data.setdefault('mail.tracking.author.' + records._name, {})
    for record in records:
        infos = []
        touched = set(initial.get(record.id) or {})
        for info, names in origins.get(record.id, []):
            if names & touched and info not in infos:
                infos.append(info)
        if infos:
            existing = bodies.get(record.id, record._track_get_default_log_message(touched))
            bodies[record.id] = Markup('').join([existing or '', *[provenance_html(i) for i in infos]])
            authors.setdefault(record.id, record.env['res.partner'].browse(infos[-1][2]))
    return track(fields_iter, initial)


class SaleOrderBusinessChatter(models.Model):
    _inherit = 'sale.order'

    def message_post(self, **kwargs):
        return super().message_post(**enrich_message(self, kwargs))

    def _message_log(self, **kwargs):
        return super()._message_log(**enrich_message(self, kwargs))

    @ormcache('self.env.uid', 'self.env.su')
    def _track_get_fields(self):
        return extra_tracking(self, super()._track_get_fields())

    def _mail_track_get_field_sequence(self, fname):
        sequence = super()._mail_track_get_field_sequence(fname)
        return sequence if isinstance(sequence, int) else 100

    def _message_track(self, fields_iter, initial_values_dict):
        return track_audit(self, fields_iter, initial_values_dict, super()._message_track)

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write, document=True)


class PurchaseOrderBusinessChatter(models.Model):
    _inherit = 'purchase.order'

    def message_post(self, **kwargs):
        return super().message_post(**enrich_message(self, kwargs))

    def _message_log(self, **kwargs):
        return super()._message_log(**enrich_message(self, kwargs))

    @ormcache('self.env.uid', 'self.env.su')
    def _track_get_fields(self):
        return extra_tracking(self, super()._track_get_fields())

    def _mail_track_get_field_sequence(self, fname):
        sequence = super()._mail_track_get_field_sequence(fname)
        return sequence if isinstance(sequence, int) else 100

    def _message_track(self, fields_iter, initial_values_dict):
        return track_audit(self, fields_iter, initial_values_dict, super()._message_track)

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write, document=True)


class AccountMoveBusinessChatter(models.Model):
    _inherit = 'account.move'

    def message_post(self, **kwargs):
        return super().message_post(**enrich_message(self, kwargs))

    def _message_log(self, **kwargs):
        return super()._message_log(**enrich_message(self, kwargs))

    @ormcache('self.env.uid', 'self.env.su')
    def _track_get_fields(self):
        return extra_tracking(self, super()._track_get_fields())

    def _mail_track_get_field_sequence(self, fname):
        sequence = super()._mail_track_get_field_sequence(fname)
        return sequence if isinstance(sequence, int) else 100

    def _message_track(self, fields_iter, initial_values_dict):
        return track_audit(self, fields_iter, initial_values_dict, super()._message_track)

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write, document=True)


class AccountPaymentBusinessChatter(models.Model):
    _inherit = 'account.payment'

    def message_post(self, **kwargs):
        return super().message_post(**enrich_message(self, kwargs))

    def _message_log(self, **kwargs):
        return super()._message_log(**enrich_message(self, kwargs))

    @ormcache('self.env.uid', 'self.env.su')
    def _track_get_fields(self):
        return extra_tracking(self, super()._track_get_fields())

    def _mail_track_get_field_sequence(self, fname):
        sequence = super()._mail_track_get_field_sequence(fname)
        return sequence if isinstance(sequence, int) else 100

    def _message_track(self, fields_iter, initial_values_dict):
        return track_audit(self, fields_iter, initial_values_dict, super()._message_track)

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write, document=True)


class StockPickingBusinessChatter(models.Model):
    _inherit = 'stock.picking'

    def message_post(self, **kwargs):
        return super().message_post(**enrich_message(self, kwargs))

    def _message_log(self, **kwargs):
        return super()._message_log(**enrich_message(self, kwargs))

    @ormcache('self.env.uid', 'self.env.su')
    def _track_get_fields(self):
        return extra_tracking(self, super()._track_get_fields())

    def _mail_track_get_field_sequence(self, fname):
        sequence = super()._mail_track_get_field_sequence(fname)
        return sequence if isinstance(sequence, int) else 100

    def _message_track(self, fields_iter, initial_values_dict):
        return track_audit(self, fields_iter, initial_values_dict, super()._message_track)

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write, document=True)


class StockLotBusinessChatter(models.Model):
    _inherit = 'stock.lot'

    def message_post(self, **kwargs):
        return super().message_post(**enrich_message(self, kwargs))

    def _message_log(self, **kwargs):
        return super()._message_log(**enrich_message(self, kwargs))

    @ormcache('self.env.uid', 'self.env.su')
    def _track_get_fields(self):
        return extra_tracking(self, super()._track_get_fields())

    def _mail_track_get_field_sequence(self, fname):
        sequence = super()._mail_track_get_field_sequence(fname)
        return sequence if isinstance(sequence, int) else 100

    def _message_track(self, fields_iter, initial_values_dict):
        return track_audit(self, fields_iter, initial_values_dict, super()._message_track)

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write, document=True)


class SaleOrderLineBusinessChatter(models.Model):
    _inherit = 'sale.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write)

    def unlink(self):
        return unlink_audit(self, super().unlink)


class PurchaseOrderLineBusinessChatter(models.Model):
    _inherit = 'purchase.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write)

    def unlink(self):
        return unlink_audit(self, super().unlink)


class AccountMoveLineBusinessChatter(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write)

    def unlink(self):
        return unlink_audit(self, super().unlink)


class StockMoveBusinessChatter(models.Model):
    _inherit = 'stock.move'

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write)

    def unlink(self):
        return unlink_audit(self, super().unlink)


class StockMoveLineBusinessChatter(models.Model):
    _inherit = 'stock.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write)

    def unlink(self):
        return unlink_audit(self, super().unlink)


class StockQuantBusinessChatter(models.Model):
    _inherit = 'stock.quant'

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write)

    def unlink(self):
        return unlink_audit(self, super().unlink)


class SaleOperationChatter(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        with operation(self, 'Confirmar venta'):
            return super().action_confirm()

    def action_cancel(self):
        with operation(self, 'Cancelar venta'):
            return super().action_cancel()


class PurchaseOperationChatter(models.Model):
    _inherit = 'purchase.order'

    def button_confirm(self):
        with operation(self, 'Confirmar compra'):
            return super().button_confirm()

    def button_cancel(self):
        with operation(self, 'Cancelar compra'):
            return super().button_cancel()


class AccountingOperationChatter(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        with operation(self, 'Contabilizar'):
            return super()._post(soft=soft)

    def button_cancel(self):
        with operation(self, 'Cancelar documento'):
            return super().button_cancel()

    def button_draft(self):
        with operation(self, 'Restablecer a borrador'):
            return super().button_draft()


class PaymentOperationChatter(models.Model):
    _inherit = 'account.payment'

    def action_post(self):
        with operation(self, 'Confirmar pago'):
            return super().action_post()

    def action_cancel(self):
        with operation(self, 'Cancelar pago'):
            return super().action_cancel()


class PickingOperationChatter(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        with operation(self, 'Validar transferencia'):
            return super().button_validate()

    def action_cancel(self):
        with operation(self, 'Cancelar transferencia'):
            return super().action_cancel()


class InventoryOperationChatter(models.Model):
    _inherit = 'stock.quant'

    def _apply_inventory(self, date=None):
        with operation(self, 'Aplicar ajuste de inventario'):
            return super()._apply_inventory(date=date)


class BankStatementLineBusinessChatter(models.Model):
    _inherit = 'account.bank.statement.line'

    @api.model_create_multi
    def create(self, vals_list):
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        return write_audit(self, vals, super().write)


class ReconciliationBusinessChatter(models.Model):
    _inherit = 'account.partial.reconcile'

    def _audit_reconciliation(self, title, removed=False):
        if not audit_enabled(self):
            return
        info = metadata(self, title)
        for partial in self:
            debit, credit = partial.debit_move_id.move_id, partial.credit_move_id.move_id
            changes = [('Importe conciliado', '—', str(partial.amount)),
                       ('Documento deudor', '—', document_label(debit)),
                       ('Documento acreedor', '—', document_label(credit))]
            if removed:
                changes = [(label, current, '—') for label, _old, current in changes]
            for move in debit | credit:
                post(move, title, changes, info)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._audit_reconciliation('Conciliación agregada')
        return records

    def unlink(self):
        # Publicar antes de borrar: un rollback elimina también la nota.
        self.check_access('unlink')
        self._audit_reconciliation('Conciliación eliminada', removed=True)
        return super().unlink()

    def write(self, vals):
        if not audit_enabled(self):
            return super().write(vals)
        before = {r.id: (r.debit_move_id.move_id | r.credit_move_id.move_id, values(r, vals)) for r in self}
        info = metadata(self, 'Modificar conciliación')
        result = super().write(vals)
        for record in self:
            parents, old = before[record.id]
            new = values(record, old)
            changes = [(field_label(record, name), text, new[name][1])
                       for name, (value, text) in old.items() if value != new[name][0]]
            for move in parents | record.debit_move_id.move_id | record.credit_move_id.move_id:
                post(move, 'Conciliación modificada', changes, info)
        return result

