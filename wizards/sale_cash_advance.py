from odoo import Command, fields, models, _
from odoo.exceptions import UserError


class SaleAdvancePaymentInv(models.TransientModel):
    _inherit = 'sale.advance.payment.inv'

    def _prepare_down_payment_invoice_values(self, order, so_lines):
        values = super()._prepare_down_payment_invoice_values(order, so_lines)
        if order.l10n_mx_edi_payment_policy == 'PPD' or order.company_id.advance_risk_enabled:
            values.update(l10n_mx_edi_payment_policy='PUE', invoice_payment_term_id=False)
        return values


class SaleCashAdvance(models.TransientModel):
    _name = 'sale.cash.advance'
    _description = 'Anticipo de cliente desde Ventas'

    order_id = fields.Many2one('sale.order', required=True, readonly=True)
    currency_id = fields.Many2one(related='order_id.currency_id')
    amount = fields.Monetary(string='Importe antes de impuestos', required=True)

    def action_create_invoice(self):
        self.ensure_one()
        order = self.order_id
        order.check_access('read')
        company = order.company_id
        product = company.advance_product_id.with_company(company)
        if not company.advance_risk_enabled or not product or not company.downpayment_account_id:
            raise UserError(_('Configura el producto y la cuenta de anticipos de clientes en esta sucursal.'))
        if order.state == 'cancel' or not order.partner_id or self.currency_id.compare_amounts(self.amount, 0) <= 0:
            raise UserError(_('Selecciona un cliente en una orden activa e ingresa un importe positivo.'))
        taxes = product.taxes_id.filtered(lambda tax: tax.company_id == company)
        taxes = order.fiscal_position_id.map_tax(taxes)
        invoice = self.env['account.move'].with_company(company).create({
            'move_type': 'out_invoice', 'company_id': company.id,
            'partner_id': (order.partner_invoice_id or order.partner_id).id,
            'currency_id': self.currency_id.id, 'invoice_origin': order.name,
            'fiscal_position_id': order.fiscal_position_id.id,
            'advance_document': True, 'invoice_payment_term_id': False,
            'l10n_mx_edi_payment_policy': 'PUE',
            'invoice_line_ids': [Command.create({
                'product_id': product.id, 'quantity': 1, 'price_unit': self.amount,
                'account_id': company.downpayment_account_id.id, 'tax_ids': [Command.set(taxes.ids)],
            })],
        })
        return invoice._action_open_moves(invoice, _('Factura de anticipo'))
