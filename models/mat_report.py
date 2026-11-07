import re

from odoo import models
from odoo.tools.misc import format_date


def header(document):
    document.ensure_one()
    sale = document._name == 'sale.order'
    partner = document.partner_invoice_id if sale else document.partner_id
    agent = document.user_id if sale else document.invoice_user_id
    if sale:
        title = 'Cotización' if document.state in ('draft', 'sent') else 'Orden de venta'
        stage = ('Cancelado' if document.state == 'cancel' else 'Facturado'
                 if document.invoice_status == 'invoiced' else title)
        fiscal = {}
    else:
        title = 'Factura'
        stage = {'draft': 'Factura en borrador', 'posted': 'Facturado', 'cancel': 'Cancelado'}[document.state]
        fiscal = document._l10n_mx_edi_get_extra_invoice_report_values()
    usage_field = document._fields.get('l10n_mx_edi_usage')
    usage = (dict(usage_field._description_selection(document.env)).get(document.l10n_mx_edi_usage, '')
             if usage_field else '')
    payment = document.l10n_mx_edi_payment_method_id
    return {
        'sale': sale, 'title': title, 'stage': stage, 'partner': partner, 'agent': agent,
        'date_label': 'Fecha de cotización' if sale and document.state in ('draft', 'sent')
                      else 'Fecha de orden' if sale else 'Fecha de factura',
        'due_label': 'Fecha de vigencia' if sale else 'Fecha de vencimiento',
        'date': format_date(document.env, document.date_order if sale else document.invoice_date)
                if (document.date_order if sale else document.invoice_date) else '',
        'due': format_date(document.env, document.validity_date if sale else document.invoice_date_due)
               if (document.validity_date if sale else document.invoice_date_due) else '',
        'origin': document.origin if sale else document.invoice_origin,
        'reference': document.client_order_ref if sale else document.ref,
        'rfc': (not sale and document.l10n_mx_edi_cfdi_customer_rfc) or partner.vat,
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


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _mat_report_header(self):
        return header(self)

    def _mat_sale_lines(self):
        return self._get_order_lines_to_report().filtered(lambda line: not (
            line.product_id.tint_role == 'colorant' and line.tint_section_id and line._tint_recipe()))

    def _mat_sale_line_values(self, line):
        quantity = line.product_uom_qty
        unit = line.tax_ids.with_context(round=False).compute_all(line.price_unit,
            currency=self.currency_id, quantity=1, product=line.product_id,
            partner=self.partner_id)['total_excluded']
        gross = line.tax_ids.compute_all(line.price_unit, currency=self.currency_id,
            quantity=quantity, product=line.product_id, partner=self.partner_id)['total_excluded']
        code = line.product_id.default_code or ''
        color = re.search(r'\+\s*\[([^\]]+)\]', line.name or '')
        if color and line.product_id.tint_role == 'base':
            code = ' + '.join(filter(None, [code, color.group(1)]))
        return {'code': code, 'unit': unit, 'discount': self.currency_id.round(gross - line.price_subtotal)}

    def _mat_sale_discount(self):
        return sum(self._mat_sale_line_values(line)['discount'] for line in self.order_line
                   if not line.display_type and line.discount)
