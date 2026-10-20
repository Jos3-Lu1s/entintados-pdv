from odoo import Command, fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPaymentRegisterPercentages(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.accounts = {}
        for code, kind in [('PRPAY', 'liability_payable'), ('PREXP', 'expense'), ('PROUT', 'asset_current')]:
            cls.accounts[kind] = cls.env['account.account'].create({
                'name': 'Payment percentage ' + kind, 'code': code, 'account_type': kind,
                'reconcile': kind != 'expense', 'company_ids': [Command.set(cls.env.company.ids)],
            })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Percentage supplier', 'phone': '5551234567', 'is_supplier': True,
            'distributed_notes': True, 'percentage_patyc': 80,
            'property_account_payable_id': cls.accounts['liability_payable'].id,
        })
        cls.purchase = cls.env['account.journal'].create({'name': 'Percentage purchases', 'code': 'PRPU', 'type': 'purchase'})
        cls.bank = cls.env['account.journal'].create({'name': 'Percentage bank', 'code': 'PRBK', 'type': 'bank'})
        cls.bank.outbound_payment_method_line_ids.payment_account_id = cls.accounts['asset_current']

    def _wizard(self, currency=None, **values):
        invoice = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id, 'journal_id': self.purchase.id,
            'invoice_date': fields.Date.today(), 'currency_id': (currency or self.env.company.currency_id).id,
            'invoice_line_ids': [Command.create({
                'name': 'Percentage purchase', 'quantity': 1, 'price_unit': 1000,
                'account_id': self.accounts['expense'].id, 'tax_ids': [Command.clear()],
            })],
        })
        invoice.action_post()
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({'journal_id': self.bank.id, **values})
        return wizard, invoice

    def test_payment_and_remaining_percentages_match_contact(self):
        wizard, _ = self._wizard()
        self.assertTrue(wizard.distributed_notes)
        self.assertAlmostEqual(wizard.payment_percentage, 0.8)
        self.assertAlmostEqual(wizard.discount_percentage, 0.2)
        self.assertEqual(wizard.amount, 800)
        self.assertEqual(wizard.payment_difference, 200)

    def test_disabled_distributed_notes_keeps_normal_payment(self):
        self.partner.distributed_notes = False
        wizard, _ = self._wizard()
        self.assertFalse(wizard.distributed_notes)
        self.assertEqual(wizard.payment_percentage, 0)
        self.assertEqual(wizard.discount_percentage, 0)
        self.assertEqual(wizard.amount, 1000)

    def test_zero_percent_is_not_treated_as_disabled(self):
        self.partner.percentage_patyc = 0
        wizard, _ = self._wizard()
        self.assertTrue(wizard.distributed_notes)
        self.assertEqual(wizard.payment_percentage, 0)
        self.assertEqual(wizard.discount_percentage, 1)
        self.assertEqual(wizard.amount, 0)
        self.assertEqual(wizard.payment_difference, 1000)

    def test_full_payment_has_zero_remaining_percentage(self):
        self.partner.percentage_patyc = 100
        wizard, _ = self._wizard()
        self.assertEqual(wizard.payment_percentage, 1)
        self.assertEqual(wizard.discount_percentage, 0)
        self.assertEqual(wizard.amount, 1000)
        self.assertEqual(wizard.payment_difference, 0)

    def test_manual_amount_is_not_discounted_a_second_time(self):
        wizard, _ = self._wizard()
        wizard.amount = wizard.custom_user_amount = 350
        wizard.custom_user_currency_id = wizard.currency_id
        wizard._compute_amount()
        self.assertEqual(wizard.amount, 350)
        self.assertEqual(wizard.payment_difference, 650)

    def test_percentage_uses_payment_currency(self):
        company_currency = self.env.company.currency_id
        currency = self.env.ref('base.EUR') if company_currency != self.env.ref('base.EUR') else self.env.ref('base.USD')
        currency.active = True
        self.env['res.currency.rate'].create({
            'currency_id': currency.id, 'company_id': self.env.company.id,
            'name': fields.Date.today(), 'rate': 2,
        })
        wizard, _ = self._wizard(currency=currency, currency_id=company_currency.id)
        converted = currency._convert(1000, company_currency, self.env.company, wizard.payment_date)
        self.assertEqual(wizard.amount, company_currency.round(converted * 0.8))

    def test_registers_actual_payment_and_keeps_remaining_bill_open(self):
        wizard, invoice = self._wizard()
        payments = wizard._create_payments()
        self.assertEqual(payments.amount, 800)
        self.assertEqual(invoice.amount_residual, 200)
        self.assertEqual(invoice.payment_state, 'partial')

    def test_percentages_are_next_to_the_corresponding_amounts(self):
        view = self.env.ref('account.view_account_payment_register_form')._get_combined_arch()
        payment = view.xpath('//div[@name="amount_div"]//field[@name="payment_percentage"]')
        remaining = view.xpath('//field[@name="payment_difference"]/following-sibling::span/field[@name="discount_percentage"]')
        self.assertEqual(len(payment), 1)
        self.assertEqual(len(remaining), 1)
        for field in payment + remaining:
            self.assertEqual(field.get('widget'), 'percentage')
            self.assertEqual(field.getparent().get('invisible'), 'not distributed_notes')
