"""Transactional checks for per-invoice reasons and historical fallback."""
import pathlib

exec(pathlib.Path(__file__).with_name('check_vendor_invoice_capture.py').read_text().split('class VendorInvoiceCapture')[0])
from odoo import Command


class LineReasons(TestVendorCreditSummary):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.registry.init_models(cls.cr, ['vendor.credit.group.line',
            'vendor.credit.group.adjustment.line'], {'module': 'entintados_pdv'}, install=False)

    def test_separate_reasons_and_accounts(self):
        invoices = self._invoice(payment_amount=None) | self._invoice(payment_amount=None)
        other = self.expense.copy({'code': 'VSLR', 'name': 'Otra cuenta QA'})
        reasons = self.env['vendor.credit.reason'].create([
            {'name': 'Motivo A QA', 'account_id': self.expense.id},
            {'name': 'Motivo B QA', 'account_id': other.id}])
        group = self.env['vendor.credit.group'].create({
            'partner_id': self.partner.id, 'supplier_reference': 'LINE-QA',
            'line_ids': [Command.create({'invoice_id': invoice.id, 'amount': 25,
                'selected_reason_id': reason.id}) for invoice, reason in zip(invoices, reasons)]})
        self.assertFalse(group.reason_id)
        group.action_create_credit()
        for line, reason in zip(group.line_ids, reasons):
            self.assertEqual(line.credit_id.ref, reason.name)
            self.assertEqual(line.credit_id.invoice_line_ids.account_id, reason.account_id)
            self.assertEqual(line.invoice_id.amount_residual, 75)
        snapshot = [(c.id, c.ref, c.amount_total, c.invoice_line_ids.account_id.id) for c in group.credit_ids]
        group._migrate_reason_catalog()
        self.assertEqual(snapshot, [(c.id, c.ref, c.amount_total, c.invoice_line_ids.account_id.id) for c in group.credit_ids])

    def test_legacy_fallback_and_editable_reason(self):
        group = self._group(self._invoice(payment_amount=None), amount=25)
        line = group.line_ids
        self.assertFalse(line.selected_reason_id)
        self.assertEqual(line.effective_reason_id, group.reason_id)
        replacement = self.env['vendor.credit.reason'].create({'name': 'Por factura QA'})
        line.effective_reason_id = replacement
        self.assertEqual(line.selected_reason_id, replacement)
        self.assertNotEqual(group.reason_id, replacement)
        group.action_create_credit()
        self.assertEqual(group.credit_ids.ref, replacement.name)

    def test_missing_reason_blocks_generation(self):
        group = self._group(self._invoice(payment_amount=None), amount=25)
        group.reason_id = False
        with self.assertRaises(UserError):
            group.action_create_credit()
        self.assertFalse(group.credit_ids)


names = ['test_separate_reasons_and_accounts', 'test_legacy_fallback_and_editable_reason',
         'test_missing_reason_blocks_generation', 'test_iva_preview_matches_posted_credit']
result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(LineReasons(name) for name in names))
raise SystemExit(0 if result.wasSuccessful() else 1)
