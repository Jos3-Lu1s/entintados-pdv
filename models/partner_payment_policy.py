from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    payment_credit_authorized = fields.Boolean(compute='_compute_payment_credit_authorized')

    @api.depends('commercial_partner_id.is_credit')
    def _compute_payment_credit_authorized(self):
        for partner in self:
            partner.payment_credit_authorized = partner.commercial_partner_id.sudo().is_credit

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    payment_credit_authorized = fields.Boolean(related='partner_id.payment_credit_authorized')
    payment_term_id = fields.Many2one(precompute=False)
    sale_allowed_payment_term_ids = fields.Many2many('account.payment.term',
        compute='_compute_sale_allowed_payment_terms')

    def _sale_term_is_deferred(self, term):
        reference = fields.Date.to_date(self.date_order) or fields.Date.context_today(self)
        return bool(term and any(line.value_amount > 0 and line._get_due_date(reference) > reference
                                 for line in term.line_ids))

    @api.depends('partner_id', 'company_id', 'l10n_mx_edi_payment_policy')
    def _compute_payment_term_id(self):
        super()._compute_payment_term_id()
        immediate = self.env.ref('account.account_payment_term_immediate')
        for order in self.filtered(lambda record: record.country_code == 'MX'):
            if order.l10n_mx_edi_payment_policy == 'PUE':
                order.payment_term_id = immediate
            elif order.l10n_mx_edi_payment_policy == 'PPD' and not order._sale_term_is_deferred(order.payment_term_id):
                order.payment_term_id = False

    @api.depends('company_id', 'l10n_mx_edi_payment_policy', 'date_order')
    def _compute_sale_allowed_payment_terms(self):
        immediate = self.env.ref('account.account_payment_term_immediate')
        for order in self:
            terms = self.env['account.payment.term'].search([
                '|', ('company_id', '=', False), ('company_id', '=', order.company_id.id)])
            if order.country_code == 'MX':
                if order.l10n_mx_edi_payment_policy == 'PPD':
                    terms = terms.filtered(order._sale_term_is_deferred)
                elif order.l10n_mx_edi_payment_policy == 'PUE':
                    terms = terms & immediate
            order.sale_allowed_payment_term_ids = terms

    @api.depends('partner_id', 'company_id')
    def _compute_l10n_mx_edi_payment_method_id(self):
        super()._compute_l10n_mx_edi_payment_method_id()
        cash = self.env.ref('l10n_mx_edi.payment_method_efectivo')
        for order in self.filtered(lambda record: record.country_code == 'MX'):
            if not order.l10n_mx_edi_payment_method_id:
                order.l10n_mx_edi_payment_method_id = cash

    @api.depends('partner_id', 'company_id')
    def _compute_l10n_mx_edi_payment_policy(self):
        super()._compute_l10n_mx_edi_payment_policy()
        for order in self.filtered(lambda record: record.country_code == 'MX'):
            if not order.l10n_mx_edi_payment_policy or (
                    order.l10n_mx_edi_payment_policy == 'PPD' and not order.payment_credit_authorized):
                order.l10n_mx_edi_payment_policy = 'PUE'

    @api.onchange('partner_id', 'l10n_mx_edi_payment_policy')
    def _onchange_sale_payment_policy(self):
        if (self.country_code == 'MX' and self.l10n_mx_edi_payment_policy == 'PPD'
                and not self.payment_credit_authorized):
            self.l10n_mx_edi_payment_policy = 'PUE'
            return {'warning': {'title': _('Crédito no autorizado'),
                                'message': _('PPD requiere crédito autorizado para el cliente.')}}

    @api.constrains('partner_id', 'company_id', 'l10n_mx_edi_payment_method_id',
                    'l10n_mx_edi_payment_policy', 'payment_term_id', 'date_order')
    def _check_sale_payment_policy(self):
        for order in self.filtered(lambda record: record.country_code == 'MX' and record.state != 'cancel'):
            if not order.l10n_mx_edi_payment_method_id or not order.l10n_mx_edi_payment_policy:
                raise ValidationError(_('La forma de pago y la política de pago son obligatorias en la cotización.'))
            if order.l10n_mx_edi_payment_policy == 'PPD' and not order.payment_credit_authorized:
                raise ValidationError(_('No puedes seleccionar PPD: el cliente no tiene crédito autorizado.'))
            if order.l10n_mx_edi_payment_policy == 'PPD' and not order._sale_term_is_deferred(order.payment_term_id):
                raise ValidationError(_('Para PPD debes seleccionar un plazo de pago en días; no se permite Pago inmediato.'))
            if (order.l10n_mx_edi_payment_policy == 'PUE'
                    and order.payment_term_id != self.env.ref('account.account_payment_term_immediate')):
                raise ValidationError(_('Las ventas de contado (PUE) deben utilizar Pago inmediato.'))

    def action_confirm(self):
        self._check_sale_payment_policy()
        return super().action_confirm()

    @api.model
    def _setup_sale_payment_defaults(self):
        cash = self.env.ref('l10n_mx_edi.payment_method_efectivo')
        for order in self.search([('state', 'in', ['draft', 'sent'])]).filtered(
                lambda record: record.country_code == 'MX'):
            values = {}
            if not order.l10n_mx_edi_payment_method_id:
                values['l10n_mx_edi_payment_method_id'] = cash.id
            if not order.l10n_mx_edi_payment_policy or (
                    order.l10n_mx_edi_payment_policy == 'PPD' and not order.payment_credit_authorized):
                values['l10n_mx_edi_payment_policy'] = 'PUE'
            if values:
                order.write(values)
