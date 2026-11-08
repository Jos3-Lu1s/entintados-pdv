from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged
from odoo.exceptions import UserError

from .common_pdf import offline_wkhtmltopdf, render_offline_pdf


@tagged('post_install', '-at_install')
class TestPpdReceivableReport(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.account_fiscal_country_id = cls.env.ref('base.us')
        cls.partner_a.write({'is_credit': True, 'credit_limit': 5000})
        cls.partner_b.is_credit = False
        cls.report = cls.env.ref('entintados_pdv.ppd_receivable_report')

    def _invoice(self, amount, policy='PPD', partner=None, post=True, move_type='out_invoice'):
        invoice = self._create_invoice(
            move_type=move_type, partner_id=partner or self.partner_a,
            invoice_date='2026-01-01',
            invoice_line_ids=[Command.create({
                'name': 'Receivable report test', 'price_unit': amount,
                'account_id': self.company_data['default_account_revenue'].id,
                'tax_ids': [Command.clear()],
            })],
        )
        if post:
            invoice.with_context(bypass_risk=True).action_post()
        invoice.l10n_mx_edi_payment_policy = policy
        return invoice

    def _options(self, cutoff='2026-01-31', unfold=False):
        return self.report.get_options({
            'selected_variant_id': self.report.id,
            'date': {'date_from': cutoff, 'date_to': cutoff, 'mode': 'single', 'filter': 'custom'},
            'unfold_all': unfold,
        })

    def _partner_line(self, options):
        return next(line for line in self.report._get_lines(options)
                    if self.report._get_model_info_from_id(line['id']) == ('res.partner', self.partner_a.id))

    def test_filters_credit_limit_and_native_report_unchanged(self):
        included = self._invoice(1000)
        self._invoice(200, policy='PUE')
        self._invoice(300, partner=self.partner_b)
        self._invoice(400, post=False)
        self._invoice(500, move_type='out_refund')
        options = self._options(unfold=True)
        self.assertEqual(options['columns'][0]['expression_label'], 'ppd_credit_limit')
        domain = self.report._get_options_domain(options, 'strict_range')
        aml = self.env['account.move.line'].search(domain)
        self.assertEqual(aml.move_id, included)
        line = self._partner_line(options)
        values = {c['expression_label']: c['no_format'] for c in line['columns']}
        self.assertEqual(values['ppd_credit_limit'], 5000)
        self.assertEqual(values['total'], 1000)
        for row in self.report._get_lines(options):
            if self.report._get_model_info_from_id(row['id'])[0] != 'res.partner':
                self.assertFalse(row['columns'][0]['no_format'])
        native = self.env.ref('account_reports.aged_receivable_report')
        self.assertNotIn('ppd_credit_limit', native.column_ids.mapped('expression_label'))
        self.assertNotIn(('move_id.l10n_mx_edi_payment_policy', '=', 'PPD'),
                         list(native._get_options_domain(options, 'strict_range')))

    def test_partial_reconciliation_cutoff_and_exports(self):
        invoice = self._invoice(1000)
        credit = self._invoice(250, move_type='out_refund')
        credit.button_draft()
        credit.write({'date': '2026-02-01', 'invoice_date': '2026-02-01'})
        credit.with_context(bypass_risk=True).action_post()
        (invoice.line_ids + credit.line_ids).filtered(
            lambda line: line.account_id.account_type == 'asset_receivable'
        ).reconcile()
        # US fixtures avoid PAC calls; restore the Mexican policy after recomputations.
        self.env.flush_all()
        invoice.l10n_mx_edi_payment_policy = 'PPD'
        for cutoff, expected in [('2026-01-31', 1000), ('2026-02-28', 750), ('2026-03-31', 750), ('2027-01-31', 750)]:
            options = self._options(cutoff, unfold=True)
            line = self._partner_line(options)
            self.assertEqual(next(c['no_format'] for c in line['columns']
                                  if c['expression_label'] == 'total'), expected)
        options = self._options('2026-02-28', unfold=True)
        self.assertTrue(self.report.export_to_xlsx(options)['file_content'])
        with offline_wkhtmltopdf(self.env):
            self.assertTrue(self.report.export_to_pdf(options)['file_content'].startswith(b'%PDF'))

    def test_setup_idempotent(self):
        counts = (len(self.report.line_ids), len(self.report.column_ids))
        self.env['account.report']._setup_ppd_receivable_report()
        self.assertEqual(self.env.ref('entintados_pdv.ppd_receivable_report'), self.report)
        self.assertEqual((len(self.report.line_ids), len(self.report.column_ids)), counts)
        for code, _name in self.env['res.lang'].get_installed():
            if code.startswith('es'):
                self.assertEqual(self.report.with_context(lang=code).name, 'Cuentas por cobrar PPD')
        options = self.report.get_options({'selected_variant_id': self.report.id})
        self.assertEqual(options['date']['date_to'], str(fields.Date.context_today(self.report)))
        self.assertEqual(options['date']['mode'], 'single')
        self.assertFalse(self.report.filter_date_range)
        self.assertEqual(self.report.filter_account_type, 'receivable')

    def test_opening_resets_remembered_date_but_keeps_manual_cutoff(self):
        remembered = self._options('2026-01-31')
        opened = self.report.get_options({**remembered, 'is_opening_report': True})
        self.assertEqual(opened['date']['date_to'], str(fields.Date.context_today(self.report)))
        self.assertEqual(opened['date']['filter'], 'today')
        legacy = self.report.get_options({
            **remembered, 'is_opening_report': True,
            'date': {'date_from': '2026-01-01', 'date_to': '2026-01-31',
                     'mode': 'range', 'filter': 'previous_month'},
        })
        self.assertEqual(legacy['date']['filter'], 'today')
        self.assertEqual(legacy['date']['mode'], 'single')
        selected = self.report.get_options({**remembered, 'is_opening_report': False})
        self.assertEqual(selected['date']['date_to'], '2026-01-31')

    def test_credit_limit_remains_on_unfolded_customer_with_subtotals(self):
        self.env.company.totals_below_sections = True
        self._invoice(1000)
        for unfold in [False, True]:
            options = self._options(unfold=unfold)
            line = self._partner_line(options)
            self.assertEqual(line['columns'][0]['no_format'], 5000)
            self.assertEqual(options['custom_display_config']['templates']['AccountReportLineCell'],
                             'entintados_pdv.PpdReceivableLineCell')
            if unfold:
                self.assertTrue(line['unfolded'])

    def test_due_date_and_overdue_days_at_cutoff(self):
        expected = {}
        for due_date, days in [('2026-01-15', 16), ('2026-01-31', 0), ('2026-02-10', 0)]:
            invoice = self._invoice(1000)
            invoice.line_ids.filtered(
                lambda aml: aml.account_id.account_type == 'asset_receivable'
            ).date_maturity = due_date
            self.env.flush_all()
            invoice.l10n_mx_edi_payment_policy = 'PPD'
            aml = invoice.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
            expected[aml.id] = (due_date, days)
        for basis in ['base_on_maturity_date', 'base_on_invoice_date']:
            options = self.report.get_options({**self._options(unfold=True), 'aging_based_on': basis})
            labels = [c['expression_label'] for c in options['columns']]
            index = labels.index('invoice_date')
            self.assertEqual(labels[index + 1:index + 3], ['ppd_due_date', 'ppd_days_overdue'])
            found = {}
            for row in self.report._get_lines(options):
                model, rid = self.report._get_model_info_from_id(row['id'])
                values = {c['expression_label']: c['no_format'] for c in row['columns']}
                if model == 'account.move.line':
                    found[rid] = (str(values['ppd_due_date']), values['ppd_days_overdue'])
                else:
                    self.assertIsNone(values['ppd_due_date'])
                    self.assertIsNone(values['ppd_days_overdue'])
            self.assertEqual(found, expected)

    def test_customer_download_wizard_filters_and_historical_balance(self):
        invoice = self._invoice(1000)
        self._invoice(200, policy='PUE')
        self.partner_b.is_credit = True
        self._invoice(700, partner=self.partner_b)
        credit = self._invoice(250, move_type='out_refund')
        credit.button_draft()
        credit.write({'date': '2026-02-01', 'invoice_date': '2026-02-01'})
        credit.with_context(bypass_risk=True).action_post()
        (invoice.line_ids + credit.line_ids).filtered(
            lambda line: line.account_id.account_type == 'asset_receivable').reconcile()
        self.env.flush_all()
        invoice.l10n_mx_edi_payment_policy = 'PPD'
        options = self._options('2026-01-31')
        self.assertTrue(any(b['action'] == 'action_download_customer_ppd' for b in options['buttons']))
        action = self.report.dispatch_report_action(options, 'action_download_customer_ppd')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action['views'], [(
            self.env.ref('entintados_pdv.ppd_receivable_download_form').id, 'form')])
        wizard = self.env['ppd.receivable.download'].with_context(action['context']).create({
            'partner_id': self.partner_a.id,
        })
        data = wizard._get_report_data()
        self.assertEqual(data['credit_limit'], 5000)
        self.assertEqual(data['total'], 1000)
        self.assertEqual([r['invoice'] for r in data['rows']], [invoice.name])
        wizard.date_to = '2026-02-28'
        self.assertEqual(wizard._get_report_data()['total'], 750)
        self.assertEqual(wizard.action_download()['report_type'], 'qweb-pdf')
        pdf, _kind = render_offline_pdf(
            self.env['ir.actions.report'], 'entintados_pdv.action_ppd_customer_pdf', wizard.ids)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.partner_b.is_credit = False
        wizard.partner_id = self.partner_b
        with self.assertRaises(UserError):
            wizard.action_download()
