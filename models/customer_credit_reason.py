from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class CustomerCreditReason(models.Model):
    _name = 'customer.credit.reason'
    _description = 'Motivo de nota de crédito de cliente'
    _order = 'sequence, name, id'

    name = fields.Char(string='Motivo', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)

    def _check_selection(self, company):
        self.ensure_one()
        self.check_access('read')
        if not self.active or (self.company_id and self.company_id != company):
            raise ValidationError(_('Selecciona un motivo de cliente activo y de la misma compañía.'))


class CustomerCreditReasonMixin(models.AbstractModel):
    _name = 'customer.credit.reason.mixin'
    _description = 'Selección de motivo de crédito de cliente'

    reason_id = fields.Many2one('customer.credit.reason', string='Motivo', ondelete='restrict', check_company=True)

    @api.onchange('reason_id')
    def _onchange_customer_credit_reason(self):
        if self.reason_id:
            self.reason = self.reason_id.name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('reason_id'):
                reason = self.env['customer.credit.reason'].browse(vals['reason_id'])
                reason._check_selection(self.env['res.company'].browse(vals.get('company_id')) or self.env.company)
                vals.setdefault('reason', reason.name)
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('reason_id'):
            reason = self.env['customer.credit.reason'].browse(vals['reason_id'])
            for record in self:
                reason._check_selection(self.env['res.company'].browse(vals.get('company_id')) or record.company_id)
            vals = dict(vals)
            vals.setdefault('reason', reason.name)
        return super().write(vals)


class CustomerAdjustmentReason(models.Model):
    _name = 'customer.account.adjustment'
    _inherit = ['customer.account.adjustment', 'customer.credit.reason.mixin']


class CustomerTicketReturnReason(models.Model):
    _name = 'pos.accounting.return'
    _inherit = ['pos.accounting.return', 'customer.credit.reason.mixin']


class AccountMoveCustomerCreditReason(models.Model):
    _inherit = 'account.move'

    customer_credit_reason_id = fields.Many2one('customer.credit.reason', string='Motivo de crédito de cliente',
                                               ondelete='restrict', check_company=True, copy=False)

    @api.constrains('customer_credit_reason_id', 'move_type', 'company_id')
    def _check_customer_credit_reason(self):
        for move in self.filtered('customer_credit_reason_id'):
            if move.move_type != 'out_refund':
                raise ValidationError(_('El motivo de cliente solo corresponde a notas de crédito de clientes.'))

    def action_post(self):
        for move in self.filtered(lambda item: item.move_type == 'out_refund' and item.customer_credit_reason_id):
            move.customer_credit_reason_id._check_selection(move.company_id)
        return super().action_post()


class AccountReversalCustomerCreditReason(models.TransientModel):
    _inherit = 'account.move.reversal'

    customer_credit_reason_id = fields.Many2one('customer.credit.reason', string='Motivo de crédito de cliente',
                                               ondelete='restrict', check_company=True)

    @api.onchange('customer_credit_reason_id')
    def _onchange_customer_credit_reason(self):
        if self.customer_credit_reason_id:
            self.reason = self.customer_credit_reason_id.name

    def _prepare_default_reversal(self, move):
        values = super()._prepare_default_reversal(move)
        if move.move_type == 'out_invoice' and self.customer_credit_reason_id:
            self.customer_credit_reason_id._check_selection(move.company_id)
            values['customer_credit_reason_id'] = self.customer_credit_reason_id.id
            if not self.reason:
                values['ref'] = '%s: %s' % (move.name, self.customer_credit_reason_id.name)
        return values
