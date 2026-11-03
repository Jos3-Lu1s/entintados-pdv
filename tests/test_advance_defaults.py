from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.convert import convert_file


@tagged('post_install', '-at_install')
class TestAdvanceDefaults(TransactionCase):
    def setUp(self):
        super().setUp()
        self.company = self.env['res.company'].create({'name': 'Automatic advance setup'})
        self.company = self.company.with_company(self.company)

    def _account(self, code, kind, reconcile=True):
        return self.env['account.account'].with_company(self.company).create({
            'code': code, 'name': code, 'account_type': kind, 'reconcile': reconcile,
            'company_ids': [Command.set(self.company.ids)],
        })

    def _box(self, **values):
        return self.env['pos.config'].with_company(self.company).create({
            'name': 'Advance automatic box', 'company_id': self.company.id,
            'payment_method_ids': [Command.clear()], **values,
        })

    def test_xml_configures_company_and_existing_box_idempotently(self):
        box = self._box()
        for _ in range(2):
            convert_file(self.env, 'entintados_pdv', 'data/advance_accounting_data.xml', {}, mode='update')
        self.company._check_advance_configuration()
        self.assertTrue(self.company.advance_risk_enabled)
        product = self.company.advance_product_id
        self.assertEqual(product.type, 'service')
        self.assertTrue(product.is_accounting_advance)
        self.assertTrue(product.available_in_pos)
        self.assertEqual(box.down_payment_product_id, product)
        self.assertEqual(product.with_company(self.company).property_account_income_id,
                         self.company.downpayment_account_id)
        self.assertEqual(product.with_company(self.company).property_account_expense_id,
                         self.company.supplier_advance_account_id)
        self.assertEqual(self.env['product.product'].search_count([
            ('company_id', '=', self.company.id), ('is_accounting_advance', '=', True)]), 1)
        self.assertEqual(self.env['account.account'].with_company(self.company).search_count([
            ('company_ids', 'in', self.company.id), ('code', 'in', ['ANTCLI', 'ANTPRO'])]), 2)

    def test_reuses_compatible_accounts_and_product(self):
        customer = self._account('ANTCLI', 'liability_current')
        supplier = self._account('ANTPRO', 'asset_current')
        product = self.env['product.product'].with_company(self.company).create({
            'name': 'Existing advance', 'default_code': 'ANTICIPO-%s' % self.company.id,
            'company_id': self.company.id, 'type': 'service', 'is_accounting_advance': True,
            'property_account_income_id': customer.id, 'property_account_expense_id': supplier.id,
        })
        self.company._prepare_advance_defaults(activate=True)
        self.assertEqual(self.company.advance_product_id, product)
        self.assertEqual(self.company.downpayment_account_id, customer)
        self.assertEqual(self.company.supplier_advance_account_id, supplier)

    def test_code_collisions_create_new_accounts_without_changing_existing(self):
        income = self._account('ANTCLI', 'income', False)
        expense = self._account('ANTPRO', 'expense', False)
        self.company.downpayment_account_id = income
        self.company._prepare_advance_defaults(activate=True)
        self.assertEqual(self.company.downpayment_account_id.code, 'ANTCLI1')
        self.assertEqual(self.company.supplier_advance_account_id.code, 'ANTPRO1')
        self.assertEqual(income.account_type, 'income')
        self.assertFalse(income.reconcile)
        self.assertEqual(expense.account_type, 'expense')

    def test_preserves_existing_box_product_and_explicit_disable(self):
        selected = self.env['product.product'].create({'name': 'Native POS advance', 'type': 'service'})
        box = self._box(down_payment_product_id=selected.id)
        self.company._prepare_advance_defaults(activate=True)
        self.company.advance_risk_enabled = False
        self.env['res.company']._setup_advance_defaults()
        self.assertFalse(self.company.advance_risk_enabled)
        self.assertEqual(box.down_payment_product_id, selected)

    def test_new_box_inherits_company_product_and_preserves_explicit_choice(self):
        self.company._prepare_advance_defaults(activate=True)
        self.assertEqual(self._box().down_payment_product_id, self.company.advance_product_id)
        self.assertFalse(self._box(down_payment_product_id=False).down_payment_product_id)

    def test_product_reference_collision_does_not_retype_existing_product(self):
        reference = 'ANTICIPO-%s' % self.company.id
        existing = self.env['product.product'].create({
            'name': 'Existing unrelated goods', 'default_code': reference, 'type': 'consu',
        })
        self.company._prepare_advance_defaults(activate=True)
        self.assertEqual(existing.type, 'consu')
        self.assertNotEqual(self.company.advance_product_id, existing)
        self.assertEqual(self.company.advance_product_id.default_code, reference + '-1')

    def test_company_accounts_and_products_are_isolated(self):
        other = self.env['res.company'].create({'name': 'Second automatic advance company'})
        self.env['res.company']._setup_advance_defaults()
        self.assertNotEqual(self.company.advance_product_id, other.advance_product_id)
        self.assertNotEqual(self.company.downpayment_account_id, other.downpayment_account_id)
        self.assertEqual(self.company.advance_product_id.company_id, self.company)
        self.assertEqual(other.advance_product_id.company_id, other)

    def test_incompatible_legacy_setup_does_not_abort_other_companies(self):
        incompatible = self._account('LEGACY', 'income', False)
        # Such partial configurations may predate the module constraints.
        self.env.cr.execute('UPDATE res_company SET supplier_advance_account_id = %s WHERE id = %s',
                            [incompatible.id, self.company.id])
        self.company.invalidate_recordset(['supplier_advance_account_id'])
        other = self.env['res.company'].create({'name': 'Company after incompatible setup'})
        self.env['res.company']._setup_advance_defaults()
        self.assertEqual(self.company.supplier_advance_account_id, incompatible)
        self.assertFalse(self.company.advance_product_id)
        self.assertTrue(other.advance_product_id)

    def test_box_restriction_does_not_undo_company_setup(self):
        self._box()
        with patch.object(type(self.env['pos.config']), 'write', side_effect=UserError('Open box restriction')):
            self.company._prepare_advance_defaults(activate=True)
        self.assertTrue(self.company.advance_product_id)
        self.company._check_advance_configuration()

    def test_incompatible_selected_product_is_preserved_without_partial_setup(self):
        product = self.env['product.product'].create({'name': 'Unmarked service', 'type': 'service'})
        self.env.cr.execute('UPDATE res_company SET advance_product_id = %s WHERE id = %s',
                            [product.id, self.company.id])
        self.company.invalidate_recordset(['advance_product_id'])
        self.env['res.company']._setup_advance_defaults()
        self.assertEqual(self.company.advance_product_id, product)
        self.assertFalse(product.is_accounting_advance)
        self.assertFalse(self.company.supplier_advance_account_id)
        self.assertFalse(self.env['account.account'].with_company(self.company).search([
            ('company_ids', 'in', self.company.id), ('code', 'in', ['ANTCLI', 'ANTPRO'])]))
