from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError


class VendorCreditGroupAdjustment(models.TransientModel):
    _name = 'vendor.credit.group.adjustment'
    _description = 'Ajuste de notas agrupadas y pagos'
    _check_company_auto = True

    group_id = fields.Many2one('vendor.credit.group', required=True, readonly=True, ondelete='cascade')
    company_id = fields.Many2one(related='group_id.company_id')
    currency_id = fields.Many2one(related='group_id.currency_id')
    revision = fields.Integer(readonly=True)
    reason_id = fields.Many2one('vendor.credit.reason', string='Motivo', required=True, check_company=True)
    explanation = fields.Text(string='Justificación del ajuste', required=True)
    line_ids = fields.One2many('vendor.credit.group.adjustment.line', 'wizard_id', string='Facturas')

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        group = self.env['vendor.credit.group'].browse(values.get('group_id') or self.env.context.get('default_group_id'))
        if group:
            group.check_access('write')
            values.update(revision=group.adjustment_revision, reason_id=group.reason_id.id,
                          line_ids=[Command.create({'source_line_id': line.id, 'amount': line.amount})
                                    for line in group.line_ids])
        return values

    def action_apply(self):
        self.ensure_one()
        if len(self.line_ids.source_line_id) != len(self.line_ids):
            raise UserError(_('No puedes repetir una factura en el ajuste.'))
        return self.group_id._apply_amount_adjustment(
            {line.source_line_id.id: line.amount for line in self.line_ids},
            self.reason_id, self.explanation or '', self.revision)


class VendorCreditGroupAdjustmentLine(models.TransientModel):
    _name = 'vendor.credit.group.adjustment.line'
    _description = 'Importe de ajuste por factura de proveedor'

    wizard_id = fields.Many2one('vendor.credit.group.adjustment', required=True, ondelete='cascade')
    source_line_id = fields.Many2one('vendor.credit.group.line', required=True, readonly=True, ondelete='cascade')
    invoice_id = fields.Many2one(related='source_line_id.invoice_id')
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    old_amount = fields.Monetary(related='source_line_id.amount', string='Nota actual')
    payment_id = fields.Many2one(related='source_line_id.payment_to_adjust_id', string='Pago a ajustar')
    amount = fields.Monetary(string='Nueva nota', required=True)
    payment_amount = fields.Monetary(string='Nuevo importe del pago', compute='_compute_payment_amount')

    @api.depends('amount', 'source_line_id.amount', 'source_line_id.invoice_id.amount_residual')
    def _compute_payment_amount(self):
        for line in self:
            payment = line.source_line_id._candidate_payment()
            line.payment_amount = (payment.amount + line.source_line_id.invoice_id.amount_residual
                                   + line.source_line_id.amount - line.amount if payment else 0)
