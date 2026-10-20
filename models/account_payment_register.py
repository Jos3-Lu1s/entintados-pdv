from odoo import models, api, fields


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    distributed_notes = fields.Boolean(related='partner_id.distributed_notes', readonly=True)
    payment_percentage = fields.Float(
        string="Porcentaje de pago", compute='_compute_discount_percentage',
        help="Porcentaje de pago configurado en el contacto.",
    )
    discount_percentage = fields.Float(
        string="Porcentaje restante",
        compute='_compute_discount_percentage',
        help="Porcentaje que se deja de pagar sobre esta factura, calculado "
             "como 100 menos el porcentaje de pago configurado en el proveedor.",
    )
    
    @api.depends('partner_id', 'partner_id.distributed_notes', 'partner_id.percentage_patyc')
    def _compute_discount_percentage(self):
        for wizard in self:
            partner = wizard.partner_id
            # The percentage widget expects fractions: 0.80 is displayed as 80%.
            wizard.payment_percentage = partner.percentage_patyc / 100.0 if partner.distributed_notes else 0.0
            wizard.discount_percentage = 1.0 - wizard.payment_percentage if partner.distributed_notes else 0.0

    @api.depends('can_edit_wizard', 'source_amount', 'source_amount_currency', 'source_currency_id',
                 'company_id', 'currency_id', 'payment_date', 'installments_mode',
                 'partner_id', 'partner_id.distributed_notes', 'partner_id.percentage_patyc')
    def _compute_amount(self):
        super()._compute_amount()
        for wizard in self:
            if (wizard.distributed_notes and wizard.can_edit_wizard and wizard.journal_id
                    and wizard.currency_id and wizard.payment_date and not wizard.custom_user_amount):
                # Keep native currency conversion/installments and manual amount overrides.
                wizard.amount = wizard.currency_id.round(wizard.amount * wizard.payment_percentage)
