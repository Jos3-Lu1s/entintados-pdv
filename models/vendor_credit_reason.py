from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class VendorCreditReason(models.Model):
    _name = 'vendor.credit.reason'
    _description = 'Motivo de nota de crédito de proveedor'
    _order = 'sequence, name, id'

    name = fields.Char(string='Motivo', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    operation_type = fields.Selection([
        ('monetary', 'Ajuste monetario'), ('return', 'Material / devolución a proveedor'),
    ], string='Tipo de afectación', default='monetary', required=True)
    account_id = fields.Many2one('account.account', string='Cuenta del ajuste', ondelete='restrict',
                                help='Cuenta que sustituye la cuenta de gasto de las líneas de la nota. '
                                     'Sin configurar se conservan las cuentas de la factura.')
    payable_account_id = fields.Many2one('account.account', string='Cuenta por pagar', ondelete='restrict',
                                        help='Debe coincidir con la cuenta por pagar de la factura para poder conciliarla. '
                                             'Sin configurar se utiliza la de la factura.')

    @api.constrains('account_id', 'payable_account_id', 'company_id')
    def _check_accounts(self):
        for reason in self:
            for account in reason.account_id | reason.payable_account_id:
                if not reason.company_id or reason.company_id not in account.company_ids:
                    raise ValidationError(_('Para configurar cuentas, selecciona su misma compañía en el motivo.'))
                if not account.active:
                    raise ValidationError(_('Las cuentas del motivo deben estar activas.'))
            if reason.account_id.account_type in ('liability_payable', 'asset_receivable', 'off_balance'):
                raise ValidationError(_('La cuenta del ajuste no puede ser por cobrar, por pagar ni de orden.'))
            if reason.payable_account_id and (
                    reason.payable_account_id.account_type != 'liability_payable'
                    or not reason.payable_account_id.reconcile):
                raise ValidationError(_('La cuenta por pagar debe ser de proveedores y permitir conciliación.'))
