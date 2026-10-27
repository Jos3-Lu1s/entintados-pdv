"""Completa el seguimiento nativo sin sustituir los mensajes del flujo."""
import hashlib
from contextvars import ContextVar

from markupsafe import Markup

from odoo import _, api, models
from odoo.tools import html2plaintext


TRACKABLE_TYPES = {
    'boolean', 'char', 'text', 'integer', 'float', 'monetary',
    'selection', 'date', 'datetime', 'many2one', 'many2many',
}
TECHNICAL_FIELDS = {'id', 'create_uid', 'create_date', 'write_uid', 'write_date', 'display_name'}
TECHNICAL_PREFIXES = ('message_', 'activity_', 'access_', 'website_', 'rating_')
NATIVE_STOCK_LOGS = ContextVar('entintados_native_stock_logs', default=())


def chatter_language(record):
    """Preferir español mexicano sin cambiar el idioma del usuario."""
    languages = record.env['res.lang'].sudo().search([
        ('active', '=', True), ('code', 'in', ['es_MX', 'es_419', 'es_ES'])])
    for code in ('es_MX', 'es_419', 'es_ES'):
        if code in languages.mapped('code'):
            return code
    return record.env.lang


SPANISH_LABELS = {
    'product_id': 'Producto', 'quantity': 'Cantidad', 'uom_id': 'Unidad de medida',
    'description': 'Descripción', 'stock_move_id': 'Movimiento generado',
    'product_uom_id': 'Unidad de medida', 'user_id': 'Responsable',
    'required': 'Obligatorio', 'sequence': 'Secuencia', 'status': 'Estado',
    'product_uom_qty': 'Cantidad solicitada', 'product_uom': 'Unidad de medida',
    'description_picking': 'Descripción', 'location_id': 'Ubicación de origen',
    'location_dest_id': 'Ubicación de destino', 'picking_id': 'Transferencia',
    'lot_id': 'Lote / número de serie', 'lot_name': 'Lote / número de serie',
    'package_id': 'Paquete de origen', 'result_package_id': 'Paquete de destino',
    'owner_id': 'Propietario', 'name': 'Nombre', 'start': 'Inicio', 'stop': 'Fin',
    'allday': 'Todo el día', 'active': 'Activo', 'partner_ids': 'Participantes',
    'crm_activity_type_id': 'Tipo de actividad',
}


def field_label(record, name):
    if name in SPANISH_LABELS:
        return SPANISH_LABELS[name]
    return record.with_context(lang=chatter_language(record)).fields_get(
        [name], attributes=['string'])[name]['string']


def extra_tracking(record, native):
    """Usar mail.thread también para los campos editables sin tracking previo."""
    extra = {
        name for name, field in record._fields.items()
        if field.store and not field.compute and not field.related
        and field.type in TRACKABLE_TYPES and name not in TECHNICAL_FIELDS
        and not name.startswith(TECHNICAL_PREFIXES)
    }
    # fields_get respeta los grupos que restringen el acceso a cada campo.
    return set(native) | set(record.fields_get(list(extra), attributes=()))


def enabled(record):
    return not (record.env.context.get('tracking_disable') or record.env.context.get('mail_notrack'))


def post_note(parent, title, changes):
    if not changes or not parent or not parent.exists():
        return
    body = Markup('<p><strong>%s</strong></p><ul>%s</ul>') % (
        title, Markup('').join(Markup('<li>%s: %s → %s</li>') % change for change in changes))
    author_id = parent.env.user.partner_id.id
    if parent._name == 'stock.picking':
        from .business_chatter import metadata, provenance_html
        info = metadata(parent)
        body += provenance_html(info)
        author_id = info[2]
    # El usuario que puede editar una línea puede carecer de permiso para
    # publicar en el documento padre. Elevar solo la escritura de la bitácora.
    parent.sudo().message_post(
        body=body, subtype_xmlid='mail.mt_note', author_id=author_id)


def special_values(record, names):
    """HTML y binarios no admiten mail.tracking.value; no guardar su contenido binario."""
    values = {}
    for name in names:
        field = record._fields.get(name)
        if not field or field.type not in ('binary', 'html') or field.compute or field.related:
            continue
        if not record._has_field_access(field, 'read'):
            continue
        value = record.with_context(bin_size=False)[name]
        if field.type == 'binary':
            raw = value.encode() if isinstance(value, str) else value or b''
            values[name] = (hashlib.sha256(raw).hexdigest(), _('Archivo presente') if value else _('Sin archivo'))
        else:
            text = html2plaintext(value or '')
            values[name] = (text, text or '—')
    return values


def write_special(records, vals, write):
    before = {r.id: special_values(r, vals) for r in records} if enabled(records) else {}
    result = write(vals)
    for record in records:
        old = before.get(record.id, {})
        new = special_values(record, old)
        changes = []
        for name, (initial, initial_label) in old.items():
            current, current_label = new[name]
            if initial != current:
                if record._fields[name].type == 'binary' and initial_label == current_label:
                    current_label = _('Archivo reemplazado')
                changes.append((field_label(record, name), initial_label, current_label))
        post_note(record, _('Documento actualizado'), changes)
    return result


class CrmLeadChatter(models.Model):
    _inherit = 'crm.lead'

    def _message_track(self, fields_iter, initial_values_dict):
        localized = self.with_context(lang=chatter_language(self))
        return super(CrmLeadChatter, localized)._message_track(fields_iter, initial_values_dict)

    def _track_get_fields(self):
        return extra_tracking(self, super()._track_get_fields())

    def _mail_track_get_field_sequence(self, fname):
        sequence = super()._mail_track_get_field_sequence(fname)
        return sequence if isinstance(sequence, int) else 100

    def write(self, vals):
        return write_special(self, vals, super().write)


class ApprovalRequestChatter(models.Model):
    _inherit = 'approval.request'

    def _message_track(self, fields_iter, initial_values_dict):
        localized = self.with_context(lang=chatter_language(self))
        return super(ApprovalRequestChatter, localized)._message_track(fields_iter, initial_values_dict)

    def _track_get_fields(self):
        return extra_tracking(self, super()._track_get_fields())

    def _mail_track_get_field_sequence(self, fname):
        sequence = super()._mail_track_get_field_sequence(fname)
        return sequence if isinstance(sequence, int) else 100

    def write(self, vals):
        return write_special(self, vals, super().write)


class StockPickingChatter(models.Model):
    _inherit = 'stock.picking'

    def _message_track(self, fields_iter, initial_values_dict):
        localized = self.with_context(lang=chatter_language(self))
        return super(StockPickingChatter, localized)._message_track(fields_iter, initial_values_dict)

    def _track_get_fields(self):
        return extra_tracking(self, super()._track_get_fields())

    def _mail_track_get_field_sequence(self, fname):
        sequence = super()._mail_track_get_field_sequence(fname)
        return sequence if isinstance(sequence, int) else 100

    def write(self, vals):
        return write_special(self, vals, super().write)


LINE_CONFIG = {
    'crm.material.line': ('lead_id', ('product_id', 'quantity', 'uom_id', 'description', 'stock_move_id')),
    'approval.product.line': ('approval_request_id', ('product_id', 'quantity', 'product_uom_id', 'description')),
    'approval.approver': ('request_id', ('user_id', 'required', 'sequence', 'status')),
    'stock.move': ('picking_id', ('product_id', 'product_uom_qty', 'product_uom', 'description_picking',
                                'location_id', 'location_dest_id', 'picking_id')),
    'stock.move.line': ('picking_id', ('product_id', 'quantity', 'product_uom_id', 'lot_id', 'lot_name',
                                     'location_id', 'location_dest_id', 'package_id', 'result_package_id', 'owner_id')),
    'calendar.event': ('crm_lead_id', ('name', 'start', 'stop', 'allday', 'active', 'user_id',
                                     'partner_ids', 'crm_activity_type_id')),
}


def line_values(record, names):
    record = record.with_context(lang=chatter_language(record))
    result = {}
    for name in names:
        field = record._fields.get(name)
        if not field or not record._has_field_access(field, 'read'):
            continue
        value = record[name]
        if field.type in ('many2one', 'many2many', 'one2many'):
            result[name] = (tuple(sorted(value.ids)), ', '.join(value.mapped('display_name')) or '—')
        elif field.type == 'selection':
            label = dict(field._description_selection(record.env)).get(value, '—')
            if record._name == 'approval.approver' and name == 'status':
                label = {'new': 'Nuevo', 'pending': 'Pendiente', 'approved': 'Aprobado',
                         'refused': 'Rechazado', 'cancel': 'Cancelado',
                         'waiting': 'En espera'}.get(value, label)
            result[name] = (value, label)
        elif field.type == 'boolean':
            result[name] = (value, _('Sí') if value else _('No'))
        else:
            result[name] = (value, str(value) if value is not False and value is not None else '—')
    return result


def line_snapshot(record, names=None):
    record = record.with_context(lang=chatter_language(record))
    parent_field, business_fields = LINE_CONFIG[record._name]
    writing = names is not None
    names = set(business_fields if names is None else names).intersection(business_fields)
    # stock.move ya registra nativamente la demanda al editar movimientos confirmados.
    if writing and record._name == 'stock.move' and record.picking_id and record.state not in ('draft', 'done'):
        names.discard('product_uom_qty')
    parent = record[parent_field]
    label = record.product_id.display_name if 'product_id' in record._fields else record.display_name
    return parent, label or record._description, line_values(record, names)


def log_created(records):
    if not enabled(records):
        return records
    for record in records:
        parent, label, values = line_snapshot(record)
        changes = [(field_label(record, name), '—', value[1]) for name, value in values.items()]
        post_note(parent, _('Línea agregada: %s', label), changes)
    return records


def write_lines(records, vals, write):
    before = {r.id: line_snapshot(r, vals) for r in records} if enabled(records) else {}
    approval_status = {
        r.id: r.request_id.request_status for r in records
    } if before and records._name == 'approval.approver' and 'status' in vals else {}
    native_logs = {}
    token = NATIVE_STOCK_LOGS.set((*NATIVE_STOCK_LOGS.get(), native_logs))
    try:
        result = write(vals)
    finally:
        NATIVE_STOCK_LOGS.reset(token)
    for record in records:
        if record.id not in before:
            continue
        old_parent, label, old = before[record.id]
        for name in native_logs.get((record._name, record.id), ()):
            old.pop(name, None)
        new_parent = record[LINE_CONFIG[record._name][0]]
        # Si cambió el estado global, el seguimiento nativo ya registra la aprobación.
        if record.id in approval_status and approval_status[record.id] != new_parent.request_status:
            old.pop('status', None)
        new = line_values(record, old)
        changes = [(field_label(record, name), value[1], new[name][1])
                   for name, value in old.items() if value[0] != new[name][0]]
        if new_parent != old_parent and not changes:
            changes = [(_('Documento'), old_parent.display_name or '—', new_parent.display_name or '—')]
        post_note(old_parent, _('Línea modificada: %s', label), changes)
        if new_parent != old_parent:
            post_note(new_parent, _('Línea trasladada: %s', label), changes or [(_('Origen'), old_parent.display_name or '—', new_parent.display_name or '—')])
    return result


def unlink_lines(records, unlink):
    before = [(r._description, line_snapshot(r)) for r in records] if enabled(records) else []
    result = unlink()
    for description, (parent, label, values) in before:
        changes = [(field_label(records, name), value[1], '—') for name, value in values.items()]
        post_note(parent, _('Línea eliminada: %s', label), changes or [(description, label, '—')])
    return result


class CrmMaterialLineChatter(models.Model):
    _inherit = 'crm.material.line'

    @api.model_create_multi
    def create(self, vals_list):
        return log_created(super().create(vals_list))

    def write(self, vals):
        return write_lines(self, vals, super().write)

    def unlink(self):
        return unlink_lines(self, super().unlink)


class ApprovalProductLineChatter(models.Model):
    _inherit = 'approval.product.line'

    @api.model_create_multi
    def create(self, vals_list):
        return log_created(super().create(vals_list))

    def write(self, vals):
        return write_lines(self, vals, super().write)

    def unlink(self):
        return unlink_lines(self, super().unlink)


class ApprovalApproverChatter(models.Model):
    _inherit = 'approval.approver'

    @api.model_create_multi
    def create(self, vals_list):
        return log_created(super().create(vals_list))

    def write(self, vals):
        return write_lines(self, vals, super().write)

    def unlink(self):
        return unlink_lines(self, super().unlink)


class StockMoveChatter(models.Model):
    _inherit = 'stock.move'

    @api.model_create_multi
    def create(self, vals_list):
        return log_created(super().create(vals_list))

    def write(self, vals):
        return write_lines(self, vals, super().write)

    def unlink(self):
        return unlink_lines(self, super().unlink)


class StockMoveLineChatter(models.Model):
    _inherit = 'stock.move.line'

    @api.model
    def _log_message(self, *args, **kwargs):
        localized = self.with_context(lang=chatter_language(self))
        result = super(StockMoveLineChatter, localized)._log_message(*args, **kwargs)
        move = args[1] if len(args) > 1 else kwargs.get('move')
        values = args[3] if len(args) > 3 else kwargs.get('vals')
        template = args[2] if len(args) > 2 else kwargs.get('template')
        if isinstance(move, models.BaseModel) and len(move) == 1 and isinstance(values, dict):
            names = {'product_uom_qty'} if template == 'stock.track_move_template' else set(values)
            for collector in NATIVE_STOCK_LOGS.get():
                collector.setdefault((move._name, move.id), set()).update(names)
        return result

    @api.model_create_multi
    def create(self, vals_list):
        return log_created(super().create(vals_list))

    def write(self, vals):
        return write_lines(self, vals, super().write)

    def unlink(self):
        return unlink_lines(self, super().unlink)


class CalendarEventChatter(models.Model):
    _inherit = 'calendar.event'

    @api.model_create_multi
    def create(self, vals_list):
        return log_created(super().create(vals_list))

    def write(self, vals):
        return write_lines(self, vals, super().write)

    def unlink(self):
        return unlink_lines(self, super().unlink)


class EvidenceAttachmentChatter(models.Model):
    _inherit = 'ir.attachment'

    def _evidence_parents(self):
        # Solo evidencias del flujo, no los adjuntos nativos del chatter.
        return self.env['stock.picking'].sudo().search([
            '|', ('evidence_attachment_ids', 'in', self.ids),
            ('evidence_use_attachment_ids', 'in', self.ids),
        ])

    def write(self, vals):
        watched = {'name', 'description', 'datas', 'raw'}
        before = {}
        if enabled(self) and watched.intersection(vals):
            for attachment in self:
                parents = attachment._evidence_parents()
                if parents:
                    before[attachment.id] = (parents, attachment.name, attachment.description, attachment.checksum)
        result = super().write(vals)
        for attachment in self:
            if attachment.id not in before:
                continue
            parents, name, description, checksum = before[attachment.id]
            changes = []
            if name != attachment.name:
                changes.append((_('Nombre'), name or '—', attachment.name or '—'))
            if description != attachment.description:
                changes.append((_('Descripción'), description or '—', attachment.description or '—'))
            if checksum != attachment.checksum:
                changes.append((_('Contenido'), _('Archivo anterior'), _('Archivo reemplazado')))
            for parent in parents:
                post_note(parent, _('Evidencia modificada: %s', name), changes)
        return result

    def unlink(self):
        before = [(a._evidence_parents(), a.name) for a in self] if enabled(self) else []
        result = super().unlink()
        native = self.env.cr.precommit.data.get('mail.tracking.stock.picking', {})
        for parents, name in before:
            for parent in parents:
                prepared = native.get(parent.id) or {}
                if {'evidence_attachment_ids', 'evidence_use_attachment_ids'}.intersection(prepared):
                    continue
                post_note(parent, _('Evidencia eliminada'), [(_('Archivo'), name or '—', '—')])
        return result
