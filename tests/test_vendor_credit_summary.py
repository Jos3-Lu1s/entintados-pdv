from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.convert import convert_file

from .test_vendor_credit_group import TestVendorCreditGroup


@tagged('post_install', '-at_install')
class TestVendorCreditSummary(TransactionCase):
    _invoice = TestVendorCreditGroup._invoice
    _group = TestVendorCreditGroup._group

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.payable = cls.env['account.account'].create({'name': 'Resumen pagar QA', 'code': 'VSPAY',
            'account_type': 'liability_payable', 'reconcile': True, 'company_ids': [Command.set(cls.company.ids)]})
        cls.expense = cls.env['account.account'].create({'name': 'Resumen gasto QA', 'code': 'VSEXP',
            'account_type': 'expense', 'company_ids': [Command.set(cls.company.ids)]})
        cls.partner = cls.env['res.partner'].create({'name': 'Proveedor resumen QA', 'is_supplier': True,
            'distributed_notes': True, 'property_account_payable_id': cls.payable.id})
        cls.journal = cls.env['account.journal'].create({'name': 'Compras resumen QA', 'code': 'VSC', 'type': 'purchase'})
        cls.payment_journal = cls.env['account.journal'].create({'name': 'Pagos resumen QA', 'code': 'VSP', 'type': 'general'})

    def _tax(self, name, amount, tax_type='iva'):
        return self.env['account.tax'].create({'name': name, 'amount': amount,
            'type_tax_use': 'purchase', 'l10n_mx_tax_type': tax_type})

    def test_reference_required_and_free_text(self):
        with self.assertRaisesRegex(UserError, 'folio de referencia'):
            self.env['vendor.credit.group'].create({'partner_id': self.partner.id, 'reason': 'Prueba'})
        group = self._group(self._invoice())
        group.supplier_reference = '  NC / Proveedor - 00042  '
        self.assertEqual(group.supplier_reference, 'NC / Proveedor - 00042')
        with self.assertRaises(UserError):
            group.supplier_reference = '  '

    def test_iva_preview_matches_posted_credit(self):
        invoice = self._invoice(tax=self._tax('IVA 16%', 16), payment_amount=58)
        group = self._group(invoice, amount=58)
        self.assertEqual(group.amount_untaxed, 50)
        self.assertEqual(group.amount_iva, 8)
        self.assertEqual(group.amount_tax, 8)
        self.assertEqual(group.amount_rounding, 0)
        group.action_create_credit()
        self.assertEqual(group.amount_untaxed, group.credit_ids.amount_untaxed)
        self.assertEqual(group.amount_tax, group.credit_ids.amount_tax)

    def test_other_taxes_withholding_and_reconciliation(self):
        taxes = self._tax('IVA 16%', 16) | self._tax('IEPS 8%', 8, 'ieps') | self._tax('Retención IVA 4%', -4)
        group = self._group(self._invoice(tax=taxes, payment_amount=60), amount=60)
        self.assertEqual(group.amount_untaxed, 50)
        self.assertEqual(group.amount_iva, 8)
        self.assertEqual(group.amount_other_taxes, 4)
        self.assertEqual(group.amount_withholding, -2)
        self.assertEqual(group.amount_untaxed + group.amount_tax + group.amount_rounding, group.amount_total)

    def test_selection_and_amount_changes_refresh_breakdown(self):
        group = self._group(self._invoice(tax=self._tax('IVA 16%', 16), payment_amount=58), amount=58)
        group.line_ids.amount = 29
        self.assertEqual(group.amount_untaxed, 25)
        self.assertEqual(group.amount_iva, 4)
        group.line_ids.selected = False
        self.assertEqual(group.amount_total, 0)
        self.assertEqual(group.amount_untaxed, 0)
        self.assertEqual(group.amount_iva, 0)

    def test_iva_uses_fiscal_classification_when_name_is_custom(self):
        tax = self._tax('Impuesto personalizado', 16)
        tax.l10n_mx_tax_type = 'iva'
        group = self._group(self._invoice(tax=tax, payment_amount=58), amount=58)
        self.assertEqual(group.amount_iva, 8)
        self.assertEqual(group.amount_other_taxes, 0)

    def test_form_places_reason_below_currency_and_hides_operation(self):
        convert_file(self.env, 'entintados_pdv', 'views/vendor_credit_wizard_views.xml', {}, mode='update')
        arch = self.env.ref('entintados_pdv.vendor_credit_wizard_form')._get_combined_arch()
        currency = arch.xpath('//group/field[@name="currency_id"]')[0]
        self.assertEqual(currency.getnext().get('name'), 'reason_id')
        self.assertEqual(arch.xpath('//field[@name="supplier_reference"]')[0].get('required'), '1')
        self.assertEqual(arch.xpath('//field[@name="operation_type"]')[0].get('invisible'), '1')
        footer = arch.xpath('//group[contains(@class,"oe_subtotal_footer")]')[0]
        self.assertTrue({'amount_untaxed', 'amount_iva', 'amount_other_taxes', 'amount_withholding',
                         'amount_rounding', 'amount_total'}.issubset({field.get('name') for field in footer}))
