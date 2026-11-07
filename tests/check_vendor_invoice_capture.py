"""Regression checks for supplier invoice capture and legacy reference edits."""
import logging
import os
import sys
import unittest
import odoo
if not hasattr(odoo, 'Command'):
    import odoo.init
from odoo import fields
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tools import config

database = sys.argv[1]
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http',
                     '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
                     '--db_password=' + os.environ['PASSWORD']])
Registry(database)
logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
from odoo.tests import common
from odoo.addons.entintados_pdv.tests.test_vendor_credit_summary import TestVendorCreditSummary
common.get_db_name = lambda: database


class VendorInvoiceCapture(TestVendorCreditSummary):
    def test_create_invoice_with_web_tax_totals(self):
        from odoo import Command
        from odoo.addons.entintados_pdv.models.business_chatter import values
        source = self._invoice(payment_amount=None)
        invoice = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id,
            'journal_id': self.journal.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Factura desde formulario QA',
                'account_id': self.expense.id, 'quantity': 1, 'price_unit': 100})],
            'tax_totals': source.tax_totals,
        })
        self.assertEqual(invoice.state, 'draft')
        self.assertEqual(invoice.amount_total, 100)
        self.assertEqual(values(invoice, ['tax_totals']), {})
        self.assertFalse(any('tax_totals' in str(message.body) for message in invoice.message_ids))

    def test_unpaid_and_partial_invoices_load_and_credit(self):
        unpaid = self._invoice(payment_amount=None)
        partial = self._invoice()
        group = self._group(unpaid, amount=30)
        group._onchange_scope()
        self.assertEqual(set(group.line_ids.invoice_id.ids), set((unpaid | partial).ids))
        group.line_ids.filtered(lambda line: line.invoice_id == partial).selected = False
        selected = group.line_ids.filtered('selected')
        selected.amount = 30
        group.action_create_credit()
        self.assertEqual(group.credit_ids.state, 'posted')
        self.assertEqual(group.credit_ids.amount_total, 30)
        self.assertEqual(unpaid.amount_residual, 70)
        self.assertFalse(group.credit_ids.amount_residual)
        self.assertFalse(selected.payment_id)

    def test_historical_blank_reference_can_be_saved_and_completed(self):
        group = self._group(self._invoice())
        self.cr.execute('UPDATE vendor_credit_group SET supplier_reference = NULL WHERE id = %s', [group.id])
        group.invalidate_recordset(['supplier_reference'])
        group.write({'supplier_reference': False, 'date': fields.Date.today()})
        self.assertFalse(group.supplier_reference)
        self.assertFalse(group.supplier_reference_required)
        with self.assertRaises(UserError):
            group.action_create_credit()
        group.supplier_reference = '  NC-HISTORICA-QA  '
        self.assertEqual(group.supplier_reference, 'NC-HISTORICA-QA')
        with self.assertRaises(UserError):
            group.supplier_reference = False

    def test_invoice_onchange_defaults_and_editable_view(self):
        from lxml import etree
        from odoo.tools.convert import convert_file
        invoice = self._invoice(payment_amount=None)
        line = self.env['vendor.credit.group.line'].new({'invoice_id': invoice.id})
        line._onchange_invoice_amount()
        self.assertEqual(line.amount, invoice.amount_residual)
        convert_file(self.env, 'entintados_pdv', 'views/vendor_credit_wizard_views.xml', {}, mode='update')
        arch = self.env.ref('entintados_pdv.vendor_credit_wizard_form')._get_combined_arch()
        listing = arch.xpath('//field[@name="line_ids"]/list')[0]
        self.assertNotEqual(listing.get('create'), '0')
        self.assertNotEqual(listing.xpath('./field[@name="invoice_id"]')[0].get('readonly'), '1')
        source = etree.fromstring(self.env.ref('entintados_pdv.vendor_credit_wizard_form').arch_db.encode())
        self.assertEqual(source.xpath('//field[@name="supplier_reference"]')[0].get('required'), 'supplier_reference_required')
        self.assertTrue(self.env['vendor.credit.group'].new({}).supplier_reference_required)


names = ['test_create_invoice_with_web_tax_totals', 'test_unpaid_and_partial_invoices_load_and_credit',
         'test_historical_blank_reference_can_be_saved_and_completed',
         'test_invoice_onchange_defaults_and_editable_view',
         'test_reference_required_and_free_text', 'test_iva_preview_matches_posted_credit']
result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(VendorInvoiceCapture(name) for name in names))
raise SystemExit(0 if result.wasSuccessful() else 1)
