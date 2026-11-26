from odoo import _, api, models

from .sale_order_line import POS_CHARGED_PRICES
from .sale_tint import _TINT_OPERATION

# Precio, descuento y origen que la caja cobró y que la línea de venta debe reflejar.
_CHARGED_PRICE_FIELDS = ('discount', 'price_origin', 'price_origin_label', 'pricing_rule_type',
                         'pricing_rule_origin', 'pricing_rule_id', 'promo_reward_id')


class PosOrderSaleSync(models.Model):
    _inherit = 'pos.order'

    @api.model
    def sync_from_ui(self, orders):
        # `pos_sale` confirms the quotation after processing the ticket; that confirmation
        # re-evaluates promotions while the ticket already counts as invoiced on the line.
        return super(PosOrderSaleSync, self.with_context(pos_charged_prices=POS_CHARGED_PRICES)).sync_from_ui(orders)

    def _sync_sale_lines_with_charged_prices(self):
        """Escribe en las líneas de venta traídas a la caja el precio y el origen cobrados.

        Corre al procesar un ticket ya cobrado, antes de que `pos_sale` confirme la cotización:
        así la confirmación y su importe pendiente usan lo que pagó el cliente.
        """
        for order in self.filtered(lambda record: record.state != 'draft'):
            charged = order.lines.filtered(
                lambda line: line.qty > 0 and line.sale_order_line_id
                and not line.sale_order_line_id.is_downpayment
                and not line.sale_order_line_id.display_type
            )
            by_sale_order = {}
            for sale_line, pos_lines in charged.grouped('sale_order_line_id').items():
                sale_order = sale_line.order_id
                if sale_order.state == 'cancel' or sale_order.pos_has_active_invoice:
                    continue
                by_sale_order.setdefault(sale_order, []).append((sale_line, pos_lines))
            for sale_order, items in by_sale_order.items():
                order._write_charged_prices(sale_order, items)

    def _write_charged_prices(self, sale_order, items):
        self.ensure_one()
        updated, conflicting = [], []
        for sale_line, pos_lines in items:
            values = {pos_line._charged_sale_line_values(sale_line) for pos_line in pos_lines}
            if len(values) != 1:
                conflicting.append(sale_line)
                continue
            vals = dict(values.pop())
            sale_line = sale_line.sudo().with_company(sale_order.company_id)
            if sale_line.tint_snapshot:
                vals['tint_snapshot'] = dict(sale_line.tint_snapshot, unit_price=vals['price_unit'])
            sale_line.with_context(
                _tint_operation=_TINT_OPERATION, skip_manual_discount_breakdown=True,
                pos_charged_prices=POS_CHARGED_PRICES,
            ).write(vals)
            updated.append(sale_line)
        if not updated and not conflicting:
            return
        reference = self.pos_reference or self.name
        body = _('Precios actualizados con lo cobrado en el ticket %s.', reference)
        if conflicting:
            body += ' ' + _(
                'No se actualizaron %(lines)s: el ticket las cobró con precios o descuentos distintos.',
                lines=', '.join(line.display_name for line in conflicting),
            )
        sale_order.sudo().message_post(body=body)


class PosOrderLineSaleSync(models.Model):
    _inherit = 'pos.order.line'

    def _charged_sale_line_values(self, sale_line):
        """Valores cobrados para `sale_line`, como tupla ordenada para poder compararlos.

        El precio del ticket va en la unidad del producto; la línea de venta, en la suya.
        """
        self.ensure_one()
        price = self.product_id.uom_id._compute_price(self.price_unit, sale_line.product_uom_id)
        values = {
            'price_unit': price,
            'technical_price_unit': price,
            'technical_discount': self.discount,
        }
        for name in _CHARGED_PRICE_FIELDS:
            value = self[name]
            values[name] = value.id if isinstance(value, models.BaseModel) else value
        return tuple(sorted(values.items()))
