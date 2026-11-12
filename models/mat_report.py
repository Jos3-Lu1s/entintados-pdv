from odoo import models
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

    def _mat_report_header(self):
        return header(self)

    def action_get_mat_preview(self):
        self.ensure_one()
        self.check_access('read')
        if self.move_type != 'out_invoice':
            return False
        attachment = self.message_main_attachment_id
        return {'url': '/report/pdf/entintados_pdv.report_invoice_live/%s' % self.id,
                'attachment_id': attachment.id}
