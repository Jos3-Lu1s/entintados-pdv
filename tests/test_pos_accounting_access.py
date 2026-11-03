from odoo import Command
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPosAccountingAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cashier = new_test_user(cls.env, login='pos_access_cashier',
                                    groups='base.group_user,point_of_sale.group_pos_user')
        cls.accountant = new_test_user(cls.env, login='pos_access_accountant',
                                       groups='base.group_user,account.group_account_invoice')
        cls.combined = new_test_user(cls.env, login='pos_access_combined',
                                     groups='base.group_user,point_of_sale.group_pos_user,account.group_account_invoice')
        cls.config = cls.env['pos.config'].create({'name': 'POS access regression'})
        cls.product = cls.env['product.product'].create({'name': 'Access service', 'type': 'service'})
        cls.method = cls.env['pos.payment.method'].create({'name': 'Access customer account'})
        cls.config.payment_method_ids = [Command.link(cls.method.id)]
        cls.session = cls.env['pos.session'].create({'config_id': cls.config.id})
        cls.order = cls.env['pos.order'].create({
            'session_id': cls.session.id, 'amount_total': 100, 'amount_tax': 0,
            'amount_paid': 100, 'amount_return': 0,
            'lines': [Command.create({'product_id': cls.product.id, 'qty': 1,
                'price_unit': 100, 'price_subtotal': 100, 'price_subtotal_incl': 100})],
            'payment_ids': [Command.create({'payment_method_id': cls.method.id, 'amount': 100})],
        })

    def test_pos_and_combined_roles_read_draft_order_lines_and_payments(self):
        self.assertEqual(self.order.state, 'draft')
        for user in (self.cashier, self.combined):
            for records in (self.order, self.order.lines, self.order.payment_ids):
                with self.subTest(user=user.login, model=records._name):
                    records.with_user(user).check_access('read')
                    self.assertEqual(records.with_user(user).search([('id', 'in', records.ids)]), records)

    def test_accounting_only_role_cannot_read_ineligible_draft(self):
        self.assertFalse(self.order.accounting_return_eligible)
        for records in (self.order, self.order.lines, self.order.payment_ids):
            with self.subTest(model=records._name):
                self.assertFalse(records.with_user(self.accountant).search([('id', 'in', records.ids)]))

    def test_combined_role_still_obeys_company_scope(self):
        other_company = self.env['res.company'].create({'name': 'Other POS access company'})
        other_config = self.env['pos.config'].with_company(other_company).create({
            'name': 'Other company POS', 'company_id': other_company.id,
            'payment_method_ids': [Command.clear()],
        })
        other_session = self.env['pos.session'].create({'config_id': other_config.id})
        other = self.env['pos.order'].create({
            'session_id': other_session.id, 'company_id': other_company.id,
            'amount_total': 100, 'amount_tax': 0, 'amount_paid': 0, 'amount_return': 0,
            'lines': [Command.create({'product_id': self.product.id, 'qty': 1,
                'price_unit': 100, 'price_subtotal': 100, 'price_subtotal_incl': 100})],
        })
        self.assertFalse(other.with_user(self.combined).search([('id', '=', other.id)]))
        self.assertFalse(other.lines.with_user(self.combined).search([('id', 'in', other.lines.ids)]))
