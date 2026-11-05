"""Check the spreadsheet catalog and actual supplier credit postings; roll back fixtures."""
import logging
import os
import sys
import unittest
import odoo
if not hasattr(odoo, 'Command'):
    import odoo.init
from odoo import Command
from odoo.modules.registry import Registry
from odoo.tools import config

database = sys.argv[1]
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http',
                     '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
                     '--db_password=' + os.environ['PASSWORD']])
Registry(database)
logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
from odoo.tests import common
from odoo.addons.entintados_pdv.tests.test_vendor_credit_group import TestVendorCreditGroup
common.get_db_name = lambda: database


class PurchaseReasonCatalog(TestVendorCreditGroup):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.registry.init_models(cls.env.cr, ['vendor.credit.reason'],
                                    {'module': 'entintados_pdv'}, install=False)

    def test_catalog_missing_accounts_and_preserved_configuration(self):
        company = self.env['res.company'].create({'name': 'Catálogo Excel cuentas QA'})
        account = self.env['account.account'].with_company(company).create({
            'name': 'Descuentos sobre compras', 'code': '502.01.00', 'account_type': 'expense',
            'company_ids': [Command.set(company.ids)]})
        Reason = self.env['vendor.credit.reason']
        existing = Reason.create({'name': 'Publicidad', 'company_id': company.id})
        Reason._setup_purchase_credit_reasons()
        reasons = Reason.search([('company_id', '=', company.id)])
        self.assertEqual(len(reasons), 4)
        self.assertIn(existing, reasons)
        discount = reasons.filtered(lambda reason: reason.name == 'Descuentos sobre compras')
        self.assertEqual(discount.account_id, account)
        self.assertFalse((reasons - discount).account_id)
        self.assertFalse(reasons.payable_account_id)
        self.assertEqual(reasons.filtered(lambda reason: reason.name == 'Devoluciones sobre compras').operation_type, 'return')
        self.assertEqual(set(reasons.mapped('reference_account_code')),
                         {'115.01.01', '502.01.00', '601.61.17', '601.50.01'})
        discount.account_id = False
        Reason._setup_purchase_credit_reasons()
        self.assertFalse(discount.account_id)
        self.assertEqual(Reason.search([('company_id', '=', company.id)]), reasons)
        from odoo.tools.convert import convert_file
        convert_file(self.env, 'entintados_pdv', 'views/vendor_credit_reason_views.xml', {}, mode='update')

    def test_note_uses_credit_account_and_supplier_debit(self):
        Reason = self.env['vendor.credit.reason']
        Reason._setup_purchase_credit_reasons()
        reason = Reason.search([('name', '=', 'Publicidad'), ('company_id', '=', self.company.id)], limit=1)
        adjustment = self.expense.copy({'name': 'Cuenta de abono Excel QA', 'code': 'EXCELABOQA'})
        reason.account_id = adjustment
        for configured in (True, False):
            if not configured:
                reason.account_id = False
            invoice = self._invoice()
            group = self._group(invoice)
            group.reason_id = reason
            group.action_create_credit()
            credit = group.credit_ids
            self.assertEqual(credit.state, 'posted')
            product_lines = credit.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
            self.assertEqual(product_lines.account_id, adjustment if configured else self.expense)
            self.assertAlmostEqual(sum(product_lines.mapped('credit')), 60, places=2)
            payable = credit.line_ids.filtered(lambda line: line.account_type == 'liability_payable')
            self.assertEqual(payable.account_id, self.payable)
            self.assertAlmostEqual(sum(payable.mapped('debit')), 60, places=2)


names = ['test_catalog_missing_accounts_and_preserved_configuration', 'test_note_uses_credit_account_and_supplier_debit']
result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(PurchaseReasonCatalog(name) for name in names))
raise SystemExit(0 if result.wasSuccessful() else 1)
