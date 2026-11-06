"""Sales invoice checkout regression checks; fixture writes are rolled back."""
from pathlib import Path
exec(Path(__file__).with_name('check_customer_payment_policy.py').read_text().split('@common.tagged')[0])
from odoo import api, Command, SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.tools.convert import convert_file
from odoo.addons.entintados_pdv.tests.test_sale_credit import TestSaleCredit
from odoo.addons.entintados_pdv.tests import test_sale_credit as credit_fixtures
from unittest.mock import patch
import logging
logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)

if '--prepare-schema' in sys.argv:
    with Registry(database).cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        env.registry.init_models(cr, ['res.company', 'res.config.settings'], {'module':'entintados_pdv'}, install=False)
        cr.commit()
    print('Cash journal configuration fields prepared')
    raise SystemExit(0)

if '--install' in sys.argv:
    with Registry(database).cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        env.registry.init_models(cr, ['res.company', 'res.config.settings'], {'module':'entintados_pdv'}, install=False)
        for filename in ('views/sale_order_views.xml', 'views/res_config_settings_sale_views.xml'):
            convert_file(env, 'entintados_pdv', filename, {}, mode='update')
        if '--cash-journal' in sys.argv:
            name = sys.argv[sys.argv.index('--cash-journal') + 1]
            journal = env['account.journal'].search([('company_id','=',env.company.id),
                ('type','=','cash'), ('active','=',True), ('name','=',name)], limit=2)
            if len(journal) != 1 or not journal.default_account_id or not journal.inbound_payment_method_line_ids.filtered(lambda method: method.code == 'manual'):
                raise UserError('Revisa la cuenta y el método de cobro del diario seleccionado.')
            env.company.sale_cash_payment_journal_id = journal
            print('Sales cash journal configured:', journal.name)
        cr.commit()
        for company in env['res.company'].search([]):
            journals = env['account.journal'].search([('company_id','=',company.id), ('type','=','cash')])
            print('Cash journals:', company.name, journals.mapped('name'))
    raise SystemExit(0)


@common.tagged('post_install', '-at_install')
class SaleAutoInvoice(TestSaleCredit):
    @classmethod
    def setUpClass(cls):
        original_user = credit_fixtures.new_test_user
        def fixture_user(env, **values):
            return original_user(env(context=dict(env.context, no_reset_password=True)), **values)
        with patch.object(credit_fixtures, 'new_test_user', fixture_user):
            super().setUpClass()
        cls.env.registry.init_models(cls.cr, ['res.company', 'res.config.settings'], {'module':'entintados_pdv'}, install=False)
        cls.cash = cls.env['account.journal'].create({'name': 'Efectivo Ventas QA', 'code':'SVQ',
            'type':'cash', 'company_id':cls.company.id})
        cls.company.sale_cash_payment_journal_id = cls.cash

    def order(self, credit=False, amount=200):
        order = self.env['sale.order'].create({'partner_id':self.partner.id,
            'l10n_mx_edi_payment_policy':'PPD' if credit else 'PUE',
            'order_line':[Command.create({'product_id':self.sale_product.id, 'price_unit':amount})]})
        order.action_confirm()
        return order

    def test_cash_paid_normal_and_retry_no_duplicate(self):
        self.company.account_fiscal_country_id = self.env.ref('base.mx')
        order = self.order()
        order.action_generate_validated_invoice()
        invoice = order.invoice_ids
        self.assertEqual(invoice.state, 'posted')
        self.assertFalse(invoice.is_advance_invoice)
        self.assertFalse(invoice.amount_residual)
        self.assertEqual(invoice.payment_state, 'paid')
        payments = invoice.reconciled_payment_ids
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments.journal_id, self.cash)
        self.assertEqual(payments.amount, invoice.amount_total)
        order.action_generate_validated_invoice()
        self.assertEqual(order.invoice_ids, invoice)
        self.assertEqual(invoice.reconciled_payment_ids, payments)

    def test_ppd_posted_unpaid_and_approval_preserved(self):
        order = self.order(credit=True)
        order.action_generate_validated_invoice()
        self.assertEqual(order.invoice_ids.state, 'posted')
        self.assertEqual(order.invoice_ids.amount_residual, 200)
        self.assertFalse(order.invoice_ids.reconciled_payment_ids)
        blocked = self.order(credit=True, amount=2000)
        action = blocked.action_generate_validated_invoice()
        self.assertEqual(blocked.invoice_ids.state, 'draft')
        self.assertTrue(blocked.invoice_ids.sale_credit_request_ids)
        self.assertEqual(action['tag'], 'display_notification')
        self.assertFalse(blocked.invoice_ids.reconciled_payment_ids)

    def test_invalid_cash_configuration_rolls_back_invoice(self):
        order = self.order()
        self.cash.active = False
        with self.assertRaises(UserError):
            order.action_generate_validated_invoice()
        self.assertFalse(order.invoice_ids)
        self.assertEqual(order.invoice_status, 'to invoice')

    def test_native_regular_wizard_and_form_buttons(self):
        order = self.order()
        wizard = self.env['sale.advance.payment.inv'].create({'sale_order_ids':[Command.set(order.ids)],
            'advance_payment_method':'delivered'})
        wizard.create_invoices()
        self.assertEqual(order.invoice_ids.payment_state, 'paid')
        convert_file(self.env, 'entintados_pdv', 'views/sale_order_views.xml', {}, mode='update')
        view = self.env.ref('entintados_pdv.view_order_form')._get_combined_arch()
        for identifier in ('create_invoice','create_invoice_percentage'):
            self.assertEqual(view.xpath('//button[@id=$id]', id=identifier)[0].get('name'),
                             'action_generate_validated_invoice')

    def test_fiscal_commit_is_disabled_only_for_checkout(self):
        from odoo.addons.l10n_mx_edi.models import l10n_mx_edi_document as native_document
        native_class = next(value for value in vars(native_document).values()
                            if isinstance(value, type) and value.__module__ == native_document.__name__
                            and '_can_commit' in value.__dict__)
        documents = self.env['l10n_mx_edi.document']
        with patch.object(native_class, '_can_commit', return_value=True):
            self.assertTrue(documents._can_commit())
            self.assertFalse(documents.with_context(sale_auto_invoice_checkout=True)._can_commit())
        info = self.partner.pos_contact_info
        self.assertTrue(info['credit_authorized'])
        self.assertTrue(info['credit_limit_text'])
        self.assertEqual(info['credit_currency'], self.partner.risk_currency_id.name)


names = ['test_cash_paid_normal_and_retry_no_duplicate', 'test_ppd_posted_unpaid_and_approval_preserved',
         'test_fiscal_commit_is_disabled_only_for_checkout',
         'test_invalid_cash_configuration_rolls_back_invoice', 'test_native_regular_wizard_and_form_buttons',
         'test_sales_native_down_payment_stays_pue_on_credit_order']
result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(SaleAutoInvoice(name) for name in names))
raise SystemExit(0 if result.wasSuccessful() else 1)
