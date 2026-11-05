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

    @api.constrains('partner_id', 'company_id', 'l10n_mx_edi_payment_method_id', 'l10n_mx_edi_payment_policy')
    def _check_sale_payment_policy(self):
        for order in self.filtered(lambda record: record.country_code == 'MX' and record.state != 'cancel'):
            if not order.l10n_mx_edi_payment_method_id or not order.l10n_mx_edi_payment_policy:
                raise ValidationError(_('La forma de pago y la política de pago son obligatorias en la cotización.'))
            if order.l10n_mx_edi_payment_policy == 'PPD' and not order.payment_credit_authorized:
                raise ValidationError(_('No puedes seleccionar PPD: el cliente no tiene crédito autorizado.'))

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
