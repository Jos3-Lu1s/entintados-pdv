import logging

from odoo import api, models


_logger = logging.getLogger(__name__)
_CURSOR_KEY = 'entintados_pdv.payment_cfdi_cron_last_invoice_id'


class AccountMovePaymentCron(models.Model):
    _inherit = 'account.move'

    @api.model
    def _cron_l10n_mx_edi_update_payments(self, batch_size=50):
        """Scan PPD invoices in bounded batches using the native payment flow.

        The persistent cursor also advances past failures, so older failures or
        unchanged invoices cannot starve later invoices. A new scan retries them.
        """
        if batch_size <= 0:
            return
        params = self.env['ir.config_parameter'].sudo()
        last_id = int(params.get_param(_CURSOR_KEY, '0'))
        moves = self.with_context(
            allowed_company_ids=self.env.user.company_ids.ids,
            bin_size=False,
        )
        domain = [
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('country_code', '=', 'MX'),
            ('l10n_mx_edi_payment_policy', '=', 'PPD'),
            ('l10n_mx_edi_cfdi_state', '=', 'sent'),
            ('company_id', 'in', self.env.user.company_ids.ids),
            ('id', '>', last_id),
        ]
        remaining = moves.search_count(domain)
        cron = self.env['ir.cron']
        if not remaining:
            params.set_param(_CURSOR_KEY, '0')
            cron._commit_progress(remaining=0)
            return
        if not cron._commit_progress(remaining=remaining):
            return
        invoices = moves.search(domain, order='id', limit=batch_size)
        for invoice in invoices:
            invoice_id, invoice_name = invoice.id, invoice.name
            try:
                # Native CFDI sending commits internally, so a savepoint cannot
                # span this call. Each invoice is its own transaction instead.
                invoice._compute_l10n_mx_edi_update_payments_needed()
                if invoice.l10n_mx_edi_update_payments_needed:
                    invoice.l10n_mx_edi_cfdi_invoice_try_update_payments()
            except Exception:
                # Recover SQL before any ORM access, including names for logs.
                # Earlier commits from successful PAC calls remain preserved.
                self.env.cr.rollback()
                _logger.exception(
                    'Error updating payment CFDIs for invoice %s (id %s)',
                    invoice_name, invoice_id,
                )
            remaining -= 1
            params.set_param(_CURSOR_KEY, str(invoice_id) if remaining else '0')
            # Commit each invoice and honour the scheduler time budget.
            if not cron._commit_progress(1, remaining=remaining):
                return
