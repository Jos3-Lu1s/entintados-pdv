from odoo import api, fields, models
from odoo.fields import Domain


HANDLER = 'account.ppd.receivable.report.handler'


class PpdReceivableReport(models.Model):
    _inherit = 'account.report'

    @api.model
    def _setup_ppd_receivable_report(self):
        """Copy the native aging hierarchy once, retaining its reconciliation engine."""
        report = self.env.ref('entintados_pdv.ppd_receivable_report', raise_if_not_found=False)
        if not report:
            report = self.env.ref('account_reports.aged_receivable_report').copy()
            self.env['ir.model.data'].create({
                'module': 'entintados_pdv', 'name': 'ppd_receivable_report',
                'model': 'account.report', 'res_id': report.id, 'noupdate': True,
            })
        report.write({
            'name': 'Cuentas por cobrar PPD',
            'root_report_id': False,
            'custom_handler_model_id': self.env['ir.model']._get_id(HANDLER),
            # Native computed filter fields are not retained by report.copy().
            # Aging must include all invoices through the cutoff, not just one month.
            'filter_date_range': False,
            'default_opening_date_filter': 'today',
            'filter_unfold_all': True,
            'filter_partner': True,
            'filter_period_comparison': False,
            'filter_account_type': 'receivable',
            'filter_hierarchy': 'never',
            'filter_multi_company': 'selector',
            'filter_show_draft': False,
        })
        # The native copy retains translated titles, including Latin American Spanish.
        for code, _name in self.env['res.lang'].get_installed():
            if code.startswith('es'):
                report.with_context(lang=code).name = 'Cuentas por cobrar PPD'
        if not report.column_ids.filtered(lambda c: c.expression_label == 'ppd_credit_limit'):
            self.env['account.report.column'].create({
                'report_id': report.id, 'name': 'Límite de crédito',
                'expression_label': 'ppd_credit_limit', 'figure_type': 'monetary',
                'sequence': 0, 'sortable': False,
            })
        extra_columns = [('ppd_due_date', 'Fecha de vencimiento', 'date'),
                         ('ppd_days_overdue', 'Días de vencimiento', 'integer')]
        for label, name, figure_type in extra_columns:
            if not report.column_ids.filtered(lambda c: c.expression_label == label):
                self.env['account.report.column'].create({
                    'report_id': report.id, 'name': name, 'expression_label': label,
                    'figure_type': figure_type, 'sortable': False,
                })
        columns = list(report.column_ids.filtered(
            lambda c: c.expression_label not in ('ppd_due_date', 'ppd_days_overdue')
        ))
        invoice_index = next(i for i, c in enumerate(columns) if c.expression_label == 'invoice_date')
        for offset, (label, _name, _figure_type) in enumerate(extra_columns, 1):
            columns.insert(invoice_index + offset,
                           report.column_ids.filtered(lambda c: c.expression_label == label))
        for index, column in enumerate(columns):
            column.sequence = index * 10

    def _init_options_date(self, options, previous_options):
        if self.custom_handler_model_name == HANDLER and previous_options.get('is_opening_report'):
            # Reset the browser's remembered date only when entering the report.
            previous_options = {**previous_options, 'date': {}}
        return super()._init_options_date(options, previous_options)

    def _get_options_domain(self, options, date_scope):
        domain = super()._get_options_domain(options, date_scope)
        if self.custom_handler_model_name == HANDLER:
            domain &= Domain([
                ('move_id.move_type', '=', 'out_invoice'),
                ('move_id.state', '=', 'posted'),
                ('move_id.l10n_mx_edi_payment_policy', '=', 'PPD'),
                ('move_id.commercial_partner_id.is_credit', '=', True),
            ])
        return domain


class PpdReceivableReportHandler(models.AbstractModel):
    _name = HANDLER
    _inherit = 'account.aged.receivable.report.handler'
    _description = 'Cuentas por cobrar PPD de clientes de crédito'

    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options)
        options['custom_display_config']['templates']['AccountReportLineCell'] = (
            'entintados_pdv.PpdReceivableLineCell'
        )

    def _custom_line_postprocessor(self, report, options, lines):
        lines = super()._custom_line_postprocessor(report, options, lines)
        partner_lines = []
        invoice_lines = []
        for line in lines:
            # Native subtotal rows share their parent's column list.
            line['columns'] = list(line['columns'])
            model, res_id = report._get_model_info_from_id(line['id'])
            if model == 'res.partner' and res_id:
                partner_lines.append((line, res_id))
            elif model == 'account.move.line' and res_id:
                invoice_lines.append((line, res_id))
        partners = self.env['res.partner'].browse([pid for _, pid in partner_lines])
        companies = self.env['res.company'].browse(report.get_report_company_ids(options))
        currency = self.env.company.currency_id
        date = fields.Date.to_date(options['date']['date_to'])
        limits = {}
        for partner in partners:
            commercial = partner.commercial_partner_id
            limits[partner.id] = sum(
                (commercial.with_company(company).risk_currency_id or company.currency_id)._convert(
                    commercial.with_company(company).credit_limit, currency, company, date,
                )
                for company in companies
            )
        # Limits belong to the customer, never to an invoice or the report total.
        for line, partner_id in partner_lines:
            for index, column in enumerate(options['columns']):
                if column['expression_label'] == 'ppd_credit_limit':
                    line['columns'][index] = report._build_column_dict(
                        limits[partner_id], column, options=options, currency=currency,
                    )
        # Use each receivable installment's actual due date, independently of aging basis.
        installments = self.env['account.move.line'].browse([rid for _, rid in invoice_lines])
        due_dates = {aml.id: aml.date_maturity or aml.move_id.invoice_date_due or aml.date
                     for aml in installments}
        for line, aml_id in invoice_lines:
            due_date = due_dates[aml_id]
            values = {'ppd_due_date': due_date,
                      'ppd_days_overdue': max((date - due_date).days, 0)}
            for index, column in enumerate(options['columns']):
                if column['expression_label'] in values:
                    line['columns'][index] = report._build_column_dict(
                        values[column['expression_label']], column, options=options,
                    )
        return lines
