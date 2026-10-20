from odoo import models, _


class L10nMxEdiDocument(models.Model):
    _inherit = 'l10n_mx_edi.document'

    def _cancel_api(self, company, cancel_reason, on_failure, on_success):
        self.ensure_one()
        root = self._get_company_cfdi_values(company)['root_company']
        cancel_states = {
            'invoice_sent': 'invoice_cancel',
            'payment_sent': 'payment_cancel',
            'ginvoice_sent': 'ginvoice_cancel',
        }
        if not root.l10n_mx_edi_pac_test_env or self.state not in cancel_states:
            return super()._cancel_api(company, cancel_reason, on_failure, on_success)
        # Test PACs may return 211 indefinitely. Simulate the confirmation too,
        # using native callbacks so fiscal history and stock returns stay linked.
        for record in self.move_id or self._get_source_records():
            record.message_post(body=_(
                'Cancelación simulada por PAC de pruebas para el UUID %s. '
                'No se envió una cancelación real al PAC ni al SAT.',
                self.attachment_uuid,
            ))
        on_success()
        if self._can_commit():
            self.env.cr.commit()

