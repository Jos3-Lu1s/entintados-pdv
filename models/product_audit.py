"""Historial de valores y procedencia, también para escrituras automáticas."""
import hashlib
from contextvars import ContextVar

from markupsafe import Markup
from odoo import api, models

from .material_chatter import TECHNICAL_FIELDS, TECHNICAL_PREFIXES, field_label


ORIGIN = ContextVar('entintados_product_origin', default=None)


def snapshot(record, names):
    result = {}
    for name in names:
        field = record._fields.get(name)
        if not field or name in TECHNICAL_FIELDS or name.startswith(TECHNICAL_PREFIXES):
            continue
        # Los campos delegados se registran en la plantilla; el costo pertenece a la variante.
        if record._name == 'product.product' and field.inherited:
            continue
        if record._name == 'product.template' and name == 'standard_price':
            continue
        value = record[name]
        if field.type in ('many2one', 'one2many', 'many2many'):
            result[name] = (tuple(value.ids), ', '.join(value.mapped('display_name')) or '—')
        elif field.type == 'binary':
            raw = record.with_context(bin_size=False)[name] or b''
            raw = raw.encode() if isinstance(raw, str) else raw
            result[name] = (hashlib.sha256(raw).hexdigest(), 'Archivo adjunto' if raw else 'Sin archivo')
        else:
            result[name] = (value, str(value) if value is not False and value is not None else '—')
    return result


def audit_write(records, vals, write):
    before = {r.id: snapshot(r, vals) for r in records}
    actor = records.env.user
    origin = ORIGIN.get() or records.env.context.get('product_audit_origin')
    if not origin:
        origin = 'Importación de productos' if records.env.context.get('import_file') else 'Edición de producto / API'
    result = write(vals)
    for record in records:
        old = before[record.id]
        new = snapshot(record, old)
        changes = []
        for name, (value, label) in old.items():
            if value != new[name][0]:
                current = new[name][1]
                if record._fields[name].type == 'binary' and label == current:
                    current = 'Archivo reemplazado'
                changes.append(Markup('<li>%s: %s → %s</li>') % (field_label(record, name), label, current))
        if changes:
            template = record if record._name == 'product.template' else record.product_tmpl_id
            body = Markup('<p><strong>Producto actualizado: %s</strong></p><p>Origen: %s · Usuario: %s · Empresa: %s</p><ul>%s</ul>') % (
                record.display_name, origin, actor.name, record.env.company.display_name, Markup('').join(changes))
            template.sudo().message_post(body=body, subtype_xmlid='mail.mt_note', author_id=actor.partner_id.id)
    return result


class ProductTemplateAudit(models.Model):
    _inherit = 'product.template'

    def write(self, vals):
        return audit_write(self, vals, super().write)


class ProductProductAudit(models.Model):
    _inherit = 'product.product'

    def write(self, vals):
        return audit_write(self, vals, super().write)


class StockMoveAudit(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        documents = self.mapped('picking_id.name') + self.mapped('origin')
        token = ORIGIN.set('Inventario / recepción: %s' % ', '.join(dict.fromkeys(filter(None, documents))))
        try:
            return super()._action_done(cancel_backorder=cancel_backorder)
        finally:
            ORIGIN.reset(token)


class PurchaseOrderAudit(models.Model):
    _inherit = 'purchase.order'

    def button_confirm(self):
        token = ORIGIN.set('Confirmación de compra: %s' % ', '.join(self.mapped('name')))
        try:
            return super().button_confirm()
        finally:
            ORIGIN.reset(token)

    def _add_supplier_to_product(self):
        token = ORIGIN.set('Proveedor actualizado desde compra: %s' % ', '.join(self.mapped('name')))
        try:
            return super()._add_supplier_to_product()
        finally:
            ORIGIN.reset(token)


class ProductSupplierAudit(models.Model):
    _inherit = 'product.supplierinfo'

    def write(self, vals):
        return audit_write(self, vals, super().write)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record, vals in zip(records, vals_list):
            details = Markup('').join(Markup('<li>%s: %s</li>') % (field_label(record, name), label)
                                     for name, (_, label) in snapshot(record, vals).items())
            record.product_tmpl_id.sudo().message_post(
                body=Markup('<p><strong>Proveedor agregado al producto</strong></p><p>Origen: %s · Usuario: %s</p><ul>%s</ul>') % (
                    ORIGIN.get() or 'Edición de proveedores / API', self.env.user.name, details),
                subtype_xmlid='mail.mt_note', author_id=self.env.user.partner_id.id)
        return records

    def unlink(self):
        for record in self:
            record.product_tmpl_id.sudo().message_post(
                body=Markup('<p>Proveedor eliminado: %s · Precio: %s %s · Origen: %s · Usuario: %s</p>') % (
                    record.partner_id.display_name, record.price, record.currency_id.name,
                    ORIGIN.get() or 'Edición de proveedores / API', self.env.user.name),
                subtype_xmlid='mail.mt_note', author_id=self.env.user.partner_id.id)
        return super().unlink()


class AccountMoveAudit(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        # Una nota nueva puede no tener número hasta que super()._post lo asigne.
        documents = [move.name or move.ref or 'Borrador #%s' % move.id for move in self]
        token = ORIGIN.set('Contabilidad: %s' % ', '.join(documents))
        try:
            return super()._post(soft=soft)
        finally:
            ORIGIN.reset(token)
