from odoo.tests.common import TransactionCase, tagged

from .test_stock_return_credit_orm import run_return_credit_checks


@tagged('post_install', '-at_install')
class TestStockReturnCredit(TransactionCase):
    def test_partial_customer_return_credit(self):
        run_return_credit_checks(self.env)
