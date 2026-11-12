from collections import defaultdict
from io import BytesIO

import xlsxwriter

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    @api.model
    def _get_monthly_vendor_report_data(self, date_from=None, date_to=None, partner_ids=None, company_ids=None,
                                        customer=False):
        today = fields.Date.context_today(self)
        date_from = fields.Date.to_date(date_from) if date_from else today.replace(day=1)
        date_to = fields.Date.to_date(date_to) if date_to else today
        if date_from > date_to:
            raise UserError(_('La fecha inicial debe ser anterior o igual a la final.'))
        domain = [
            ('company_id', 'in', company_ids or self.env.company.ids),
            ('partner_type', '=', 'customer' if customer else 'supplier'),
            ('payment_type', '=', 'inbound' if customer else 'outbound'), ('state', 'in', ['in_process', 'paid']),
            ('date', '>=', date_from), ('date', '<=', date_to),
        ]
        if partner_ids:
            domain.append(('partner_id', 'in', [int(partner_id) for partner_id in partner_ids]))
        payments = self.search(domain, order='date, id')
        months = payments._monthly_payment_rows()
        rows = (row for section in months.values() for row in section['rows'])
        providers = {}
        totals = defaultdict(float)
        for payment, row in zip(payments, rows):
            month = payment.date.strftime('%Y-%m')
            group = providers.setdefault((month, payment.partner_id.id, row[-2]), {
                'id': payment.id, 'name': row[2], 'month': month,
                'currency': row[-2], 'amount': 0, 'payments': [],
            })
            group['payments'].append({'id': payment.id, 'values': row})
            group['amount'] += row[-1]
            totals[row[-2]] += row[-1]
        return {
            'date_from': date_from, 'date_to': date_to, 'ids': payments.ids,
            'providers': list(providers.values()), 'totals': dict(totals),
        }

    monthly_vendor_group_ids = fields.Many2many('vendor.payment.group', string='Pagos agrupados',
                                                compute='_compute_monthly_vendor_documents')
    monthly_vendor_bill_ids = fields.Many2many('account.move', string='Facturas',
                                               compute='_compute_monthly_vendor_documents')
    monthly_vendor_payment_kind = fields.Char(string='Tipo de pago', compute='_compute_monthly_vendor_payment_kind')

    def _compute_monthly_vendor_payment_kind(self):
        for payment in self:
            bills = payment.monthly_vendor_bill_ids
            advances = bills.filtered('is_advance_invoice')
            advance_account = (payment.company_id.downpayment_account_id if payment.partner_type == 'customer'
                               else payment.company_id.supplier_advance_account_id)
            is_advance = bool(advances or (advance_account and advance_account in payment.move_id.line_ids.account_id))
            payment.monthly_vendor_payment_kind = (
                (_('Mixto') if bills - advances else _('Anticipo')) if is_advance
                else (_('Pago de cliente') if payment.partner_type == 'customer' else _('Pago de proveedor'))
            )

    def _compute_monthly_vendor_documents(self):
        groups = defaultdict(lambda: self.env['vendor.payment.group'])
        bills = defaultdict(lambda: self.env['account.move'])
        for group in self.env['vendor.payment.group'].search([('payment_ids', 'in', self.ids)]):
            for payment in group.payment_ids & self:
                groups[payment.id] |= group
        for bill in self.env['account.move'].search([
            ('move_type', 'in', ['in_invoice', 'out_invoice']), ('matched_payment_ids', 'in', self.ids),
        ]):
            for payment in bill.matched_payment_ids & self:
                bills[payment.id] |= bill
        for payment in self:
            payment.monthly_vendor_group_ids = groups[payment.id]
            customer = payment.partner_type == 'customer'
            reconciled = payment.reconciled_invoice_ids if customer else payment.reconciled_bill_ids
            move_type = 'out_invoice' if customer else 'in_invoice'
            payment.monthly_vendor_bill_ids = (reconciled | bills[payment.id]).filtered(
                lambda invoice: invoice.move_type == move_type
            )

    def _monthly_payment_rows(self):
        self.check_access('read')
        months = {}
        for payment in self.sorted(lambda item: (item.date, item.id)):
            bills = payment.monthly_vendor_bill_ids
            kind = payment.monthly_vendor_payment_kind
            section = months.setdefault(payment.date.strftime('%Y-%m'), {'rows': [], 'totals': defaultdict(float)})
            section['rows'].append((
                payment.date, payment.name, payment.partner_id.display_name, kind,
                payment.journal_id.display_name, payment.payment_method_line_id.name,
                payment.l10n_mx_edi_payment_method_id.display_name or '',
                ', '.join(bills.mapped('name')), ', '.join(payment.monthly_vendor_group_ids.mapped('name')),
                payment.currency_id.name, payment.amount,
            ))
            section['totals'][payment.currency_id.name, kind] += payment.amount
        return months

    def _monthly_payment_headers(self):
        return [_('Fecha de pago'), _('Pago'), _('Proveedor'), _('Tipo'), _('Diario'), _('Método del diario'),
                _('Forma SAT'), _('Facturas'), _('Pagos agrupados'), _('Moneda'), _('Importe')]

    def _monthly_payment_xlsx(self):
        months = self._monthly_payment_rows()
        output = BytesIO()
        with xlsxwriter.Workbook(output, {'in_memory': True, 'strings_to_formulas': False,
                                         'strings_to_urls': False}) as workbook:
            heading = workbook.add_format({'bold': True, 'bg_color': '#E3EDF6'})
            date = workbook.add_format({'num_format': 'dd/mm/yyyy'})
            amount = workbook.add_format({'num_format': '#,##0.00'})
            detail = workbook.add_worksheet(_('Pagos mensuales'))
            detail.set_column('A:L', 23)
            detail.write_row(0, 0, [_('Mes'), *self._monthly_payment_headers()], heading)
            row = 1
            for month, section in months.items():
                for payment_date, *values in section['rows']:
                    detail.write(row, 0, month)
                    detail.write_datetime(row, 1, payment_date, date)
                    detail.write_row(row, 2, values[:-1])
                    detail.write_number(row, 11, values[-1], amount)
                    row += 1
            detail.freeze_panes(1, 0)
            detail.autofilter(0, 0, row - 1, 11)
            row += 1
            detail.write_row(row, 0, [_('Mes'), _('Moneda'), _('Tipo'), _('Total mensual')], heading)
            for month, section in months.items():
                for (currency, kind), total in section['totals'].items():
                    row += 1
                    detail.write_row(row, 0, [month, currency, kind])
                    detail.write_number(row, 3, total, amount)
        return output.getvalue()

    def action_monthly_vendor_payment_pdf(self):
        self.check_access('read')
        return {'type': 'ir.actions.act_url', 'target': 'new',
                'url': '/report/pdf/entintados_pdv.vendor_monthly_payment_pdf/' + ','.join(map(str, self.ids))}

    def action_monthly_vendor_payment_xlsx(self):
        self.check_access('read')
        return {'type': 'ir.actions.act_url', 'target': 'download',
                'url': '/entintados_pdv/vendor-monthly-payments/' + ','.join(map(str, self.ids)) + '/xlsx'}

class VendorMonthlyPaymentReportHandler(models.AbstractModel):
    _name = 'account.vendor.monthly.payment.report.handler'
    _inherit = 'account.report.custom.handler'
    _description = 'Informe contable de pagos mensuales a proveedores'

    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options)
        if not (previous_options or {}).get('date'):
            today = fields.Date.context_today(self)
            options['date'] = report._get_dates_period(today.replace(day=1), today, 'range', options_return=True)
            options['date']['filter'] = 'custom'

    def _vendor_report_data(self, options):
        dates = options['date']
        customer_report = self.env.ref('entintados_pdv.customer_monthly_payment_account_report', raise_if_not_found=False)
        return self.env['account.payment']._get_monthly_vendor_report_data(
            dates['date_from'], dates['date_to'], options.get('partner_ids'),
            [company['id'] for company in options.get('companies', [])],
            customer=bool(customer_report and options['report_id'] == customer_report.id),
        )

    def _vendor_report_columns(self, report, options, values, currency=False):
        return [report._build_column_dict(values.get(column['expression_label']), column, options=options,
                                          currency=currency) for column in options['columns']]

    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals, warnings=None):
        data = self._vendor_report_data(options)
        currencies = {currency.name: currency for currency in self.env['res.currency'].search([
            ('name', 'in', list(data['totals'])),
        ])}
        lines = []
        for provider in data['providers']:
            line_id = report._get_generic_line_id('account.payment', provider['id'], markup='vendor_payment_group')
            lines.append((0, {
                'id': line_id, 'name': f"{provider['name']} · {provider['month']} · {provider['currency']}",
                'level': 2, 'unfoldable': True,
                'unfolded': line_id in options.get('unfolded_lines', []) or options.get('unfold_all'),
                'expand_function': '_report_expand_unfoldable_line_vendor_payment',
                'columns': self._vendor_report_columns(report, options, {'amount': provider['amount']},
                                                        currencies.get(provider['currency'])),
            }))
        for code, amount in data['totals'].items():
            lines.append((0, {
                'id': report._get_generic_line_id(None, None, markup=f'vendor_payment_total_{code}'),
                'name': _('Total %s', code), 'level': 1,
                'columns': self._vendor_report_columns(report, options, {'amount': amount}, currencies.get(code)),
            }))
        return lines

    def _report_expand_unfoldable_line_vendor_payment(self, line_dict_id, groupby, options, progress,
                                                      offset, unfold_all_batch_data=None):
        report = self.env['account.report'].browse(options['report_id'])
        anchor = report._parse_line_id(line_dict_id)[-1][2]
        provider = next((item for item in self._vendor_report_data(options)['providers'] if item['id'] == anchor), None)
        lines = []
        if provider and not offset:
            currency = self.env['res.currency'].search([('name', '=', provider['currency'])], limit=1)
            labels = ('date', 'payment', 'partner', 'kind', 'journal', 'method', 'sat', 'bills', 'groups', 'currency', 'amount')
            for payment in provider['payments']:
                values = dict(zip(labels, payment['values']))
                lines.append({
                    'id': report._get_generic_line_id('account.payment', payment['id'], parent_line_id=line_dict_id),
                    'parent_id': line_dict_id, 'name': values['payment'], 'level': 4,
                    'columns': self._vendor_report_columns(report, options, values, currency),
                })
        return {'lines': lines, 'offset_increment': len(lines), 'has_more': False, 'progress': progress}
