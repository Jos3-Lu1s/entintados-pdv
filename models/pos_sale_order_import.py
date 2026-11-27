from odoo import _, models
from odoo.exceptions import UserError


def _merge_pos_records(result, extra):
    """Agrega a `result` los registros de `extra` por modelo, sin repetir ids."""
    for model, records in extra.items():
        if not isinstance(records, list):
            result.setdefault(model, records)
            continue
        current = result.setdefault(model, [])
        known = {record.get('id') for record in current}
        current.extend(record for record in records if record.get('id') not in known)


class SaleOrderPosImport(models.Model):
    _inherit = 'sale.order'

    def load_sale_order_from_pos(self, config_id):
        """Valida que la cotización se pueda traer a la caja antes de enviarla."""
        self.check_access('read')
        if 'to invoice' in self.mapped('invoice_status'):
            raise UserError(_('Esta orden está Por facturar y no se puede traer al Punto de Venta.'))
        if any(self.mapped('pos_has_active_invoice')):
            raise UserError(_('Esta cotización o pedido ya tiene factura y no se puede traer al Punto de Venta.'))
        for order in self:
            # The POS evaluates native rewards on its own and skips lines brought from Sales.
            if order.order_line.filtered('reward_id'):
                raise UserError(_('La cotización %s trae regalos de promoción: cóbrala desde Ventas.') % order.name)
        self._pos_check_importable_combos()
        colorants = self._pos_tint_colorants()
        result = super().load_sale_order_from_pos(config_id)
        if colorants:
            # The tinted base is repriced in the POS with today's price of each colorant.
            extra = self.env['product.template'].load_product_from_pos(
                config_id, [('product_variant_ids.id', 'in', colorants.ids)])
            _merge_pos_records(result, extra)
        return result

    def _pos_tint_colorants(self):
        """Colorantes de las fórmulas congeladas de la cotización; rechaza los archivados."""
        colorants = self.env['product.product']
        for order in self:
            for line in order.order_line.filtered('tint_snapshot'):
                for dose in line._tint_recipe().get('doses', []):
                    colorant = colorants.with_context(active_test=False).browse(dose['product_id']).exists()
                    if not colorant.active:
                        raise UserError(_(
                            'El entintado «%(product)s» de la cotización %(quotation)s usa el colorante '
                            'archivado %(colorant)s: cóbralo desde Ventas.',
                            product=line.product_id.display_name, quotation=order.name,
                            colorant=colorant.display_name or dose.get('name') or dose['product_id'],
                        ))
                    colorants |= colorant
        return colorants

    def _pos_check_importable_combos(self):
        """Rechaza los combos que la caja no puede rearmar igual que en la cotización."""
        for order in self:
            for package in order.order_line.filtered(lambda line: line.product_id.type == 'combo'):
                reason = package._pos_combo_import_issue()
                if reason:
                    raise UserError(_(
                        'El combo «%(combo)s» de la cotización %(quotation)s no se puede traer a la caja: '
                        '%(reason)s. Cóbralo desde Ventas.',
                        combo=package.product_id.display_name, quotation=order.name, reason=reason,
                    ))


class SaleOrderLinePosImport(models.Model):
    _inherit = 'sale.order.line'

    def _pos_combo_import_issue(self):
        """Motivo por el que este paquete de combo no se puede traer a la caja, o `False`."""
        self.ensure_one()
        items = self.linked_line_ids
        combos = self.product_id.product_tmpl_id.sudo().combo_ids
        if not items or any(not item.combo_item_id or item.combo_item_id.combo_id not in combos
                            for item in items):
            return _('cambió su configuración')
        if not all((self | items).product_id.mapped('active')):
            return _('tiene productos archivados')
        if any(line.qty_delivered or line.qty_invoiced for line in self | items):
            return _('ya tiene entregas o facturas')
        return False


class PosOrderLinePosImport(models.Model):
    _inherit = 'pos.order.line'

    def _is_imported_tint_colorant(self):
        """Hija de colorante de una base entintada traída de Ventas, o de su devolución.

        Solo muestra la fórmula en el ticket: el consumo sale de la fórmula congelada de la venta.
        """
        self.ensure_one()
        base = self.combo_parent_id
        if not base:
            return False
        sale_line = base.sale_order_line_id or base.refunded_orderline_id.sale_order_line_id
        return bool(sale_line and sale_line._tint_recipe().get('base_only'))
