from odoo import models, api, fields, _

class AccountMove(models.Model):
    _inherit = 'account.move'

    vendor_credit_source_ids = fields.Many2many(
        'account.move', 'vendor_credit_source_rel', 'credit_id', 'invoice_id',
        string='Facturas de origen', copy=False, check_company=True,
    )
    
    vendor_credit_note_ids = fields.Many2many(
        'account.move', 'vendor_credit_source_rel', 'invoice_id', 'credit_id',
        string='Notas de crédito relacionadas', copy=False, check_company=True,
    )

    vendor_credit_source_count = fields.Integer(compute='_compute_vendor_credit_counts')
    vendor_credit_note_count = fields.Integer(compute='_compute_vendor_credit_counts')

    @api.depends('vendor_credit_source_ids', 'vendor_credit_note_ids')
    def _compute_vendor_credit_counts(self):
        for move in self:
            move.vendor_credit_source_count = len(move.vendor_credit_source_ids)
            move.vendor_credit_note_count = len(move.vendor_credit_note_ids)

    @api.depends(
        'amount_residual', 'move_type', 'state', 'company_id',
        'reconciled_payment_ids.state',
        'vendor_credit_note_ids', 'vendor_credit_note_ids.state',
        'vendor_credit_source_ids', 'vendor_credit_source_ids.state',
        'line_ids.matched_debit_ids', 'line_ids.matched_credit_ids',
    )
    def _compute_payment_state(self):
        super()._compute_payment_state()
        for move in self:
            if (move.state != 'posted'
                    or move.move_type not in ('in_invoice', 'in_refund')
                    or move.payment_state not in ('in_payment', 'reversed')
                    or not move.currency_id.is_zero(move.amount_residual)):
                continue
            related = (move.vendor_credit_note_ids if move.move_type == 'in_invoice'
                       else move.vendor_credit_source_ids).filtered(
                lambda counterpart: counterpart.state == 'posted'
            )
            if not related:
                continue
            payable_lines = move.line_ids.filtered(
                lambda line: line.account_id.account_type == 'liability_payable'
            )
            partials = (payable_lines.matched_debit_ids
                        | payable_lines.matched_credit_ids).filtered(
                lambda partial: partial.amount > 0
            )
            counterparts = (partials.debit_move_id.move_id
                            | partials.credit_move_id.move_id) - move
            # El saldo está liquidado con una nota del lote. El pago bancario
            # conserva su propio estado y su conciliación pendiente.
            if counterparts & related:
                move.payment_state = 'paid'

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
        notes = self.vendor_credit_note_ids
        return self._action_open_moves(notes, _('Notas de crédito'))