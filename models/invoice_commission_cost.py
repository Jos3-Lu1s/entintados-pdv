from odoo import _, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    commission_cost = fields.Monetary(
        string='Costo histórico para comisiones', currency_field='company_currency_id',
        readonly=True, copy=False,
    )
    commission_cost_captured = fields.Boolean(readonly=True, copy=False)
    osel_commission_currency_id = fields.Many2one('res.currency', compute='_compute_osel_commission')
    osel_commission_margin = fields.Float(string='Margen', compute='_compute_osel_commission')
    osel_commission_rate = fields.Float(string='Porcentaje de comisión', compute='_compute_osel_commission')
    osel_commission_amount = fields.Monetary(
        string='Comisión', compute='_compute_osel_commission', currency_field='osel_commission_currency_id',
    )
    osel_commission_available = fields.Boolean(compute='_compute_osel_commission')
    osel_payment_date = fields.Date(string='Fecha último pago', compute='_compute_osel_commission')
    osel_cost = fields.Monetary(string='Costo', compute='_compute_osel_commission', currency_field='osel_commission_currency_id')
    osel_before_delay = fields.Monetary(string='Comisión antes de cobranza', compute='_compute_osel_commission', currency_field='osel_commission_currency_id')
    osel_credit_days = fields.Integer(string='Días de crédito', compute='_compute_osel_commission')
    osel_credit_days_label = fields.Char(string='Días de crédito', compute='_compute_osel_commission')
    osel_payment_days = fields.Integer(string='Días de cobro', compute='_compute_osel_commission')
    osel_collection_delay = fields.Integer(string='Demora cobranza', compute='_compute_osel_commission')

    def _compute_osel_commission(self):
        values = {}
        if self.env.user.has_group('account.group_account_manager'):
            values, _seller_values = self.env['account.sales.collection.report.handler']._commission_values(
                self, {'date': {'date_to': fields.Date.context_today(self)}}, historical=True,
            )
        for invoice in self:
            result = values.get(invoice.id, {})
            invoice.osel_commission_currency_id = self.env.company.currency_id
            invoice.osel_commission_available = 'commission_cost' in result
            invoice.osel_commission_margin = result.get('commission_margin', 0) / 100
            invoice.osel_commission_rate = result.get('commission_rate', 0) / 100
            invoice.osel_commission_amount = result.get('commission_amount', 0)
            invoice.osel_payment_date = result.get('commission_payment_date', False)
            invoice.osel_cost = result.get('commission_cost', 0)
            invoice.osel_before_delay = result.get('commission_before_delay', 0)
            invoice.osel_credit_days = result.get('commission_credit_days', 0)
            invoice.osel_credit_days_label = (
                str(invoice.osel_credit_days) if invoice.osel_credit_days else _('Pago Inmediato')
            ) if invoice.invoice_date and invoice.invoice_date_due and invoice.osel_payment_date else False
            invoice.osel_payment_days = result.get('commission_payment_days', 0)
            invoice.osel_collection_delay = result.get('commission_delay', 0)

    def _historical_commission_cost(self, capture=False):
        self.ensure_one()
        if self.commission_cost_captured:
            return self.commission_cost
        cogs = self.line_ids.filtered(lambda line: line.display_type == 'cogs' and line.balance > 0)
        if cogs:
            return sum(cogs.mapped('balance'))
        if not capture:
            return None
        return sum(
            line.product_id.uom_id._compute_price(line.product_id.standard_price, line.product_uom_id) * line.quantity
            for line in self.with_company(self.company_id).invoice_line_ids
            if line.display_type == 'product' and line.product_id and line.product_uom_id
        )

    def _post(self, soft=True):
        drafts = self.filtered(lambda move: move.move_type == 'out_invoice' and move.state == 'draft')
        posted = super()._post(soft=soft)
        for invoice in (posted & drafts).sudo():
            if not invoice.commission_cost_captured:
                invoice.write({
                    'commission_cost': invoice._historical_commission_cost(capture=True),
                    'commission_cost_captured': True,
                })
        return posted
