from base64 import b64encode

from odoo import models
from odoo.tools import file_open
from odoo.tools.misc import format_date


def header(document):
    document.ensure_one()
    partner = document.partner_id
    fiscal = document._l10n_mx_edi_get_extra_invoice_report_values()
    usage_field = document._fields.get('l10n_mx_edi_usage')
    usage = (dict(usage_field._description_selection(document.env)).get(document.l10n_mx_edi_usage, '')
             if usage_field else '')
    payment = document.l10n_mx_edi_payment_method_id
    return {
        'title': 'Factura',
        'stage': {'draft': 'Factura en borrador', 'posted': 'Facturado', 'cancel': 'Cancelado'}[document.state],
        'partner': partner,
        'agent': document.invoice_user_id,
        'date_label': 'Fecha de factura',
        'due_label': 'Fecha de vencimiento',
        'date': format_date(document.env, document.invoice_date) if document.invoice_date else '',
        'due': format_date(document.env, document.invoice_date_due) if document.invoice_date_due else '',
        'origin': document.invoice_origin,
        'reference': document.ref,
        'rfc': document.l10n_mx_edi_cfdi_customer_rfc or partner.vat,
        'usage': ' '.join(filter(None, [fiscal.get('usage'), fiscal.get('usage_desc')])) or usage,
        'payment_way': fiscal.get('payment_way') or payment.display_name or '',
        'payment_method': fiscal.get('payment_method') or document.l10n_mx_edi_payment_policy or '',
        'bank_account': fiscal.get('bank_account'),
    }


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _mat_invoice_logo(self):
        with file_open('entintados_pdv/static/src/img/quotation_osel_logo.jpg', 'rb') as logo:
            return 'data:image/jpeg;base64,' + b64encode(logo.read()).decode('ascii')

    def _mat_invoice_footer_image(self):
        return self.env['sale.order']._quotation_report_image(watermark=True)

    def _mat_report_header(self):
        return header(self)

    def action_print_pdf(self):
        self.ensure_one()
        if self._mat_has_signed_invoice():
            # Render from the signed XML even if an earlier PDF was saved as an attachment.
            return self.env.ref('entintados_pdv.action_report_invoice_live').report_action(self, config=False)
        return super().action_print_pdf()

    def _mat_has_signed_invoice(self):
        self.ensure_one()
        return (self.move_type == 'out_invoice' and self.l10n_mx_edi_cfdi_state == 'sent'
                and bool(self.l10n_mx_edi_cfdi_attachment_id))

    def _get_invoice_legal_documents(self, filetype, allow_fallback=False):
        if filetype == 'pdf' and self._mat_has_signed_invoice():
            reports = self.env['ir.actions.report'].with_context(website_id=False, force_report_rendering=True)
            pdf, _ = reports._render_qweb_pdf('entintados_pdv.report_invoice_live', self.ids)
            return {
                'filename': f"{(self.name or str(self.id)).replace('/', '_')}.pdf",
                'filetype': 'pdf',
                'content': pdf,
            }
        return super()._get_invoice_legal_documents(filetype, allow_fallback=allow_fallback)

    def _get_invoice_legal_documents_all(self, allow_fallback=False):
        if not self._mat_has_signed_invoice():
            return super()._get_invoice_legal_documents_all(allow_fallback=allow_fallback)

        documents = super()._get_invoice_legal_documents_all()
        if not documents:
            documents = [self._get_invoice_legal_documents('cfdi')]
        saved = self.invoice_pdf_report_id
        return [self._get_invoice_legal_documents('pdf'), *(
            document for document in documents
            if document and (document['filetype'], document['filename']) != (saved.mimetype, saved.name)
        )]

    def action_get_mat_preview(self):
        self.ensure_one()
        self.check_access('read')
        if self.move_type != 'out_invoice':
            return False
        attachment = self.message_main_attachment_id
        return {'url': '/report/pdf/entintados_pdv.report_invoice_live/%s' % self.id,
                'attachment_id': attachment.id}
