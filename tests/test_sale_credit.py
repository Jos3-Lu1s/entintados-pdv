from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .test_advance_accounting import TestAdvanceAccounting


@tagged('post_install', '-at_install')
class TestSaleCredit(TestAdvanceAccounting):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.authorizer = new_test_user(
            cls.env, login='sales_credit_qa_approver', groups='base.group_user,approvals.group_approval_user',
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.outsider = new_test_user(
            cls.env, login='sales_credit_qa_outsider', groups='base.group_user,approvals.group_approval_user',
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.env['hr.employee'].create({
            'name': cls.authorizer.name, 'user_id': cls.authorizer.id, 'company_id': cls.company.id,
            'department_id': cls.env.ref('hr.dep_administration').id,
        })
        cls.sale_product = cls.env['product.product'].create({
            'name': 'Servicio QA Ventas', 'type': 'service', 'invoice_policy': 'order',
            'property_account_income_id': cls.accounts['income'].id,
            'taxes_id': [Command.clear()],
        })

    def _sale_invoice(self, amount, credit=True):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'company_id': self.company.id,
            'l10n_mx_edi_payment_policy': 'PPD' if credit else 'PUE',
            'order_line': [Command.create({'product_id': self.sale_product.id, 'price_unit': amount})],
        })
        order.action_confirm()
        invoice = order._create_invoices()
        self.assertEqual(invoice.state, 'draft')
        return invoice

    def test_sales_generic_advance_is_paid_and_applied_explicitly(self):
        invoice = self._sale_invoice(1100)
        order = self.env['sale.order'].create({'partner_id': self.partner.id, 'company_id': self.company.id})
        self.assertFalse(order.order_line)
        wizard = self.env['sale.cash.advance'].create({'order_id': order.id, 'amount': 300})
        advance = self.env['account.move'].browse(wizard.action_create_invoice()['res_id'])
        self.assertEqual(advance.invoice_line_ids.product_id, self.advance_product)
        self.assertEqual(advance.l10n_mx_edi_payment_policy, 'PUE')
        self.assertEqual(advance.advance_available, 0)
        self._pay(advance)
        self.assertEqual(advance.advance_available, 300)
        invoice.action_post()
        request = invoice.sale_credit_request_ids
        self.assertEqual(request.amount, 1100)
        self.assertEqual(invoice.state, 'draft')
        self._apply(advance, invoice, 100)
        invoice.action_post()
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(invoice.amount_residual, 1000)
        self.assertEqual(advance.advance_available, 200)
        self.assertEqual(request.request_status, 'cancel')

    def test_sales_native_approval_permissions_and_revision(self):
        self.partner.risk_invoice_draft_include = True
        invoice = self._sale_invoice(1100)
        invoice.action_post()
        request = invoice.sale_credit_request_ids
        self.assertEqual(request.request_status, 'pending')
        self.assertEqual(request.sale_credit_projected, 1100)
        with self.assertRaises(UserError):
            invoice.with_context(bypass_risk=True)._post()
        with self.assertRaises(AccessError):
            request.approver_ids.filtered(lambda line: line.user_id == self.authorizer).with_user(self.outsider).write({'status': 'approved'})
        request.with_user(self.authorizer).action_approve()
        self.assertEqual(request.request_status, 'approved')
        invoice.invoice_line_ids.price_unit = 1200
        invoice.action_post()
        self.assertEqual(invoice.sale_credit_request_ids, request)
        self.assertEqual(request.amount, 1200)
        self.assertEqual(request.request_status, 'pending')
        request.with_user(self.authorizer).action_approve()
        invoice.action_post()
        self.assertEqual(invoice.state, 'posted')
        self.assertTrue(request.sale_credit_consumed)

    def test_sales_rejection_requires_explicit_new_folio(self):
        invoice = self._sale_invoice(1100)
        invoice.action_post()
        rejected = invoice.sale_credit_request_ids
        rejected.with_user(self.authorizer).action_refuse()
        self.assertEqual(rejected.request_status, 'refused')
        invoice.action_post()
        self.assertEqual(invoice.sale_credit_request_ids, rejected)
        invoice.action_request_sale_credit()
        replacement = invoice.sale_credit_request_ids - rejected
        self.assertEqual(replacement.request_status, 'pending')
        self.assertNotEqual(replacement.name, rejected.name)

    def test_sales_ppd_requires_credit_and_preserves_cash_flow(self):
        self.partner.is_credit = False
        invoice = self._sale_invoice(500)
        with self.assertRaises(UserError):
            invoice.action_post()
        cash = self._sale_invoice(500, credit=False)
        cash.l10n_mx_edi_payment_policy = 'PUE'
        cash.action_post()
        self.assertEqual(cash.state, 'posted')
        self.assertFalse(cash.sale_credit_request_ids)

    def test_sales_ppd_abono_reconciles_existing_invoice(self):
        invoice = self._sale_invoice(500)
        invoice.action_post()
        self.assertEqual(invoice.state, 'posted')
        bank = self.journals['bank']
        bank.inbound_payment_method_line_ids.payment_account_id = self.accounts['outstanding']
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({'journal_id': bank.id, 'amount': 125})
        payment = wizard._create_payments()
        self.assertEqual(payment.amount, 125)
        self.assertEqual(invoice.amount_residual, 375)
        self.assertEqual(invoice.invoice_line_ids.sale_line_ids.order_id.invoice_ids, invoice)

    def test_sales_cancel_voids_unused_approval(self):
        invoice = self._sale_invoice(1100)
        invoice.action_post()
        request = invoice.sale_credit_request_ids
        request.with_user(self.authorizer).action_approve()
        invoice.button_cancel()
        self.assertEqual(invoice.state, 'cancel')
        self.assertEqual(request.request_status, 'cancel')
        self.assertFalse(request.sale_credit_consumed)

    def test_sales_native_down_payment_stays_pue_on_credit_order(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'company_id': self.company.id, 'l10n_mx_edi_payment_policy': 'PPD',
            'order_line': [Command.create({'product_id': self.sale_product.id, 'price_unit': 800})],
        })
        order.action_confirm()
        wizard = self.env['sale.advance.payment.inv'].create({
            'sale_order_ids': [Command.set(order.ids)], 'advance_payment_method': 'fixed', 'fixed_amount': 200,
        })
        wizard.create_invoices()
        advance = order.invoice_ids
        self.assertTrue(advance.is_advance_invoice)
        self.assertEqual(advance.l10n_mx_edi_payment_policy, 'PUE')
        self._pay(advance)
        invoice = order._create_invoices(final=True)
        self.assertEqual(invoice.l10n_mx_edi_payment_policy, 'PPD')
        self.assertEqual(invoice.advance_source_ids, advance)
        self.assertEqual(invoice.amount_total, 600)
        invoice.action_post()
        self.assertEqual(invoice.state, 'posted')

    def test_sales_batch_cannot_bypass_customer_limit(self):
        first = self._sale_invoice(600)
        second = self._sale_invoice(600)
        with self.assertRaises(UserError):
            (first | second).action_post()
        first.action_post()
        self.assertEqual(first.state, 'posted')
        second.action_post()
        self.assertEqual(second.state, 'draft')
        self.assertEqual(second.sale_credit_request_ids.sale_credit_projected, 1200)

    def test_sales_grouping_keeps_pue_and_ppd_separate(self):
        orders = self.env['sale.order'].create([{
            'partner_id': self.partner.id, 'company_id': self.company.id,
            'l10n_mx_edi_payment_policy': policy,
            'order_line': [Command.create({'product_id': self.sale_product.id, 'price_unit': 500})],
        } for policy in ('PUE', 'PPD')])
        orders.action_confirm()
        invoices = orders._create_invoices()
        self.assertEqual(len(invoices), 2)
        self.assertEqual(set(invoices.mapped('l10n_mx_edi_payment_policy')), {'PUE', 'PPD'})
