from odoo import models, api, fields, _

class AccountMove(models.Model):
    _inherit = 'account.move'

    vendor_credit_source_ids = fields.Many2many(
        'account.move', 'vendor_credit_source_rel', 'credit_id', 'invoice_id',
        string='Facturas de origen', copy=False, check_company=True,
    )
    
    vendor_credit_source_count = fields.Integer(compute='_compute_vendor_credit_counts')
    vendor_credit_note_count = fields.Integer(compute='_compute_vendor_credit_counts')

    @api.depends('vendor_credit_source_ids')
    def _compute_vendor_credit_counts(self):
        for move in self:
            move.vendor_credit_source_count = len(move.vendor_credit_source_ids)
            move.vendor_credit_note_count = (
                self.env['account.move'].search_count(
                    [('vendor_credit_source_ids', 'in', move._origin.id)]
                ) if move._origin.id else 0
            )

    def _action_open_moves(self, moves, name):
        action = {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', moves.ids)],
        }
        if len(moves) == 1:
            action.update(view_mode='form', res_id=moves.id)
        return action

    def action_view_vendor_credit_sources(self):
        self.ensure_one()
        return self._action_open_moves(self.vendor_credit_source_ids, _('Facturas de origen'))

    def action_view_vendor_credit_notes(self):
        self.ensure_one()
        notes = self.env['account.move'].search([('vendor_credit_source_ids', 'in', self.id)])
        return self._action_open_moves(notes, _('Notas de crédito grupales'))