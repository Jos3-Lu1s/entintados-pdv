from odoo import models, api, fields


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'
    
    discount_percentage = fields.Float(
        string="Descuento (%)",
        compute='_compute_discount_percentage',
        help="Porcentaje que se deja de pagar sobre esta factura, calculado "
             "como 100 menos el porcentaje de pago configurado en el proveedor.",
    )
    
    @api.depends('partner_id', 'partner_id.distributed_notes', 'partner_id.percentage_patyc')
    def _compute_discount_percentage(self):
        for wizard in self:
            partner = wizard.partner_id
            if partner and partner.distributed_notes and partner.percentage_patyc:
                wizard.discount_percentage = 100.0 - partner.percentage_patyc
            else:
                wizard.discount_percentage = 0.0

    def _compute_amount(self):
        super()._compute_amount()
        for wizard in self:
            partner = wizard.partner_id
            if partner and partner.distributed_notes and partner.percentage_patyc:
                base_amount = wizard.source_amount_currency or wizard.source_amount
                wizard.amount = base_amount * (partner.percentage_patyc / 100.0)