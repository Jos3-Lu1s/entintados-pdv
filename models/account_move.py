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

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        to_stamp = posted.filtered(
            lambda m: m.move_type == "out_invoice"
            and m.country_code == "MX"
            and m.invoice_line_ids.sale_line_ids
            and m.l10n_mx_edi_cfdi_state != "sent"
        )
        if to_stamp:
            to_stamp._l10n_mx_edi_cfdi_invoice_try_send()
        return posted

    def _l10n_mx_edi_cfdi_invoice_try_cancel(self, document, cancel_reason):
        self.ensure_one()
        if (
            self.state == 'posted'
            and self.l10n_mx_edi_cfdi_state == 'sent'
            and self.move_type == 'out_invoice'
            and self.l10n_mx_edi_payment_policy == 'PPD'
        ):
            # Use the fiscal history, including payments no longer reconciled.
            payments = self.env['l10n_mx_edi.document'].search([
                ('invoice_ids', 'in', self.ids),
                ('state', '=', 'payment_sent'),
                ('move_id.company_id', '=', self.company_id.id),
            ])
            self.env['res.company']._with_locked_records(
                self | payments.move_id | payments.invoice_ids
            )
            failed_payments = []
            # Snapshot before cancelling: native payment cancellation can reset
            # the move and change the reconciliation/document relationships.
            sources = [(doc, doc.move_id, doc.attachment_id.id) for doc in payments]
            for payment_document, payment_move, attachment_id in sources:
                cancel_domain = [
                    ('state', '=', 'payment_cancel'),
                    ('move_id', '=', payment_move.id),
                    ('attachment_id', '=', attachment_id),
                ]
                # Sent documents remain in the history after cancellation.
                if self.env['l10n_mx_edi.document'].search_count(cancel_domain):
                    continue
                payment_move._l10n_mx_edi_cfdi_invoice_try_cancel_payment(
                    payment_document
                )
                if not self.env['l10n_mx_edi.document'].search_count(cancel_domain):
                    failed_payments.append(payment_move.display_name)
            if failed_payments:
                # Raising would roll back the history of successful PAC calls.
                self._l10n_mx_edi_cfdi_invoice_document_cancel_failed(
                    _(
                        'No se canceló la factura porque no fue posible cancelar '
                        'los complementos de pago %(payment)s. Revise el error '
                        'en sus documentos CFDI y reintente la cancelación.',
                        payment=', '.join(failed_payments),
                    ),
                    document,
                    cancel_reason,
                )
                return
        return super()._l10n_mx_edi_cfdi_invoice_try_cancel(document, cancel_reason)
