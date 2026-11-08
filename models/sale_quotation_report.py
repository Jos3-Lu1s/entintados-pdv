from odoo import models
from odoo.tools.misc import format_date, format_datetime


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _quotation_report_values(self):
        self.ensure_one()
        customer = self.partner_id.commercial_partner_id
        regime_field = customer._fields.get('l10n_mx_edi_fiscal_regime')
        regime = (dict(regime_field._description_selection(self.env)).get(customer.l10n_mx_edi_fiscal_regime, '')
                  if regime_field and customer.l10n_mx_edi_fiscal_regime else '')
        products = self.order_line.filtered(lambda line: not line.display_type)
        gross = sum(line._quotation_gross_amount() for line in products)
        is_quotation = self.state in ('draft', 'sent')
        return {
            'title': 'Cotización' if is_quotation else 'Orden de venta',
            'document_label': 'cotización' if is_quotation else 'orden de venta',
            'is_quotation': is_quotation,
            'customer': customer,
            'attention': self.partner_id.name if self.partner_id != customer else False,
            'regime': regime,
            'date_short': format_date(self.env, self.date_order, date_format='d MMM y') if self.date_order else '',
            'date_order': format_datetime(self.env, self.date_order, dt_format='dd/MM/yyyy HH:mm') if self.date_order else '',
            'validity': format_date(self.env, self.validity_date, date_format='dd/MM/yyyy') if self.validity_date else '',
            'show_discount': any(products.mapped('discount')),
            'gross': gross,
            'discount': self.currency_id.round(gross - self.amount_untaxed),
        }


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _quotation_unit_price(self):
        """Precio unitario sin impuestos y antes de descuento."""
        self.ensure_one()
        return self.tax_ids.with_context(round=False).compute_all(
            self.price_unit, currency=self.currency_id, quantity=1,
            product=self.product_id, partner=self.order_id.partner_id)['total_excluded']

    def _quotation_gross_amount(self):
        self.ensure_one()
        return self.tax_ids.compute_all(
            self.price_unit, currency=self.currency_id, quantity=self.product_uom_qty,
            product=self.product_id, partner=self.order_id.partner_id)['total_excluded']

    def _quotation_section_values(self):
        """Total de una sección y, en un entintado, la cantidad de su base."""
        self.ensure_one()
        qty = False
        if self.tint_snapshot:
            base = self.order_id.order_line.filtered(
                lambda line: line.tint_section_id == self
                and line.product_id.id == self.tint_snapshot.get('base_id'))[:1]
            qty = base.product_uom_qty if base else False
        return {'total': self._get_section_totals('price_subtotal'), 'qty': qty}
