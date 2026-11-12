"""Validate Sales payment terms in transactions that are rolled back."""
from pathlib import Path
exec(Path(__file__).with_name('check_customer_payment_policy.py').read_text().split('@common.tagged')[0])
from odoo import Command
from odoo.tools.convert import convert_file

if '--install' in sys.argv:
    from odoo import api, SUPERUSER_ID
    with Registry(database).cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        convert_file(env, 'entintados_pdv', 'views/sale_order_views.xml', {}, mode='update')
        cr.commit()
    print('Sales payment term view installed in ' + database)
    raise SystemExit(0)


@common.tagged('post_install', '-at_install')
class SalePaymentDays(common.TransactionCase):
    def setUp(self):
        super().setUp()
        self.immediate = self.env.ref('account.account_payment_term_immediate')
        self.days = self.env['account.payment.term'].create({'name': '15 días QA',
            'line_ids': [Command.create({'value': 'percent', 'value_amount': 100, 'nb_days': 15})]})
        self.partner = self.env['res.partner'].create({'name': 'Plazo Ventas QA',
            'phone': '5551234567', 'is_customer': True, 'is_credit': True, 'credit_limit': 1000})

    def test_cash_default_does_not_modify_contact(self):
        self.partner.property_payment_term_id = self.days
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.assertEqual(order.payment_term_id, self.immediate)
        self.assertEqual(self.partner.property_payment_term_id, self.days)
        self.assertEqual(order.sale_allowed_payment_term_ids, self.immediate)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            order.payment_term_id = self.days

    def test_ppd_requires_days_and_propagates_to_invoice(self):
        for term in (False, self.immediate.id):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.env['sale.order'].create({'partner_id': self.partner.id,
                    'l10n_mx_edi_payment_policy': 'PPD', 'payment_term_id': term})
        order = self.env['sale.order'].create({'partner_id': self.partner.id,
            'l10n_mx_edi_payment_policy': 'PPD', 'payment_term_id': self.days.id})
        self.assertIn(self.days, order.sale_allowed_payment_term_ids)
        self.assertNotIn(self.immediate, order.sale_allowed_payment_term_ids)
        self.assertEqual(order._prepare_invoice()['invoice_payment_term_id'], self.days.id)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            order.payment_term_id = self.immediate
        order.l10n_mx_edi_payment_policy = 'PUE'
        self.assertEqual(order.payment_term_id, self.immediate)

    def test_onchange_clears_immediate_for_ppd(self):
        order = self.env['sale.order'].new({'partner_id': self.partner.id,
            'l10n_mx_edi_payment_policy': 'PUE'})
        self.assertEqual(order.payment_term_id, self.immediate)
        order.l10n_mx_edi_payment_policy = 'PPD'
        self.assertFalse(order.payment_term_id)
        self.assertIn(self.days, order.sale_allowed_payment_term_ids._origin)

    def test_unauthorized_credit_still_rejected(self):
        self.partner.is_credit = False
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['sale.order'].create({'partner_id': self.partner.id,
                'l10n_mx_edi_payment_policy': 'PPD', 'payment_term_id': self.days.id})

    def test_installments_and_view(self):
        term = self.env['account.payment.term'].create({'name': 'Mitad ahora y mitad 30 días QA',
            'line_ids': [Command.create({'value': 'percent', 'value_amount': 50, 'nb_days': days})
                         for days in (0, 30)]})
        order = self.env['sale.order'].create({'partner_id': self.partner.id,
            'l10n_mx_edi_payment_policy': 'PPD', 'payment_term_id': term.id})
        self.assertTrue(order._sale_term_is_deferred(term))
        convert_file(self.env, 'entintados_pdv', 'views/sale_order_views.xml', {}, mode='update')
        arch = self.env.ref('entintados_pdv.sale_order_payment_policy_required')._get_combined_arch()
        self.assertIn('sale_allowed_payment_term_ids', arch.xpath('//field[@name="payment_term_id"]')[0].get('domain'))


suite = unittest.defaultTestLoader.loadTestsFromTestCase(SalePaymentDays)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
