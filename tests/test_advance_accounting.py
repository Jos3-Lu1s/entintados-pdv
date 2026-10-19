from unittest.mock import patch

from odoo import Command, fields
from odoo.addons.entintados_pdv.models.pos_order import PosOrder as ExistingPosOrder
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAdvanceAccounting(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.accounts = {}
        for key, account_type in (
            ('receivable', 'asset_receivable'), ('payable', 'liability_payable'),
            ('customer', 'liability_current'), ('supplier', 'asset_current'),
            ('expense', 'expense'), ('income', 'income'),
            ('outstanding', 'asset_current'),
            ('vat', 'asset_current'),
        ):
            cls.accounts[key] = cls.env['account.account'].create({
                'name': 'Advance test ' + key, 'code': 'ADV' + key.upper(),
                'account_type': account_type, 'reconcile': key not in ('expense', 'income'),
                'company_ids': [Command.set(cls.company.ids)],
            })
        cls.company.write({
            'downpayment_account_id': cls.accounts['customer'].id,
            'supplier_advance_account_id': cls.accounts['supplier'].id,
            'advance_risk_enabled': True,
            'account_fiscal_country_id': cls.env.ref('base.us').id,
            'account_default_pos_receivable_account_id': cls.accounts['receivable'].id,
        })
        cls.advance_product = cls.env['product.product'].create({
            'name': 'Test advance', 'default_code': 'TEST-ADVANCE', 'type': 'service',
            'is_accounting_advance': True, 'available_in_pos': True,
            'property_account_income_id': cls.accounts['customer'].id,
            'property_account_expense_id': cls.accounts['supplier'].id,
            'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
        })
        cls.company.advance_product_id = cls.advance_product
        cls.partner = cls.env['res.partner'].create({
            'name': 'Advance test partner', 'phone': '5551234567', 'is_customer': True,
            'is_supplier': True, 'is_credit': True, 'credit_limit': 1000,
            'risk_invoice_open_include': True, 'risk_invoice_unpaid_include': True,
            'property_account_receivable_id': cls.accounts['receivable'].id,
            'property_account_payable_id': cls.accounts['payable'].id,
        })
        cls.journals = {}
        for key, journal_type in (('sale', 'sale'), ('purchase', 'purchase'), ('payment', 'general'), ('bank', 'bank')):
            cls.journals[key] = cls.env['account.journal'].create({
                'name': 'Advance test ' + key, 'code': 'AD' + key[:3].upper(),
                'type': journal_type, 'company_id': cls.company.id,
            })

    def _invoice(self, amount, supplier=False, advance=False, tax=None):
        account = self.accounts['supplier' if supplier else 'customer'] if advance else self.accounts['expense' if supplier else 'income']
        move = self.env['account.move'].create({
            'move_type': 'in_invoice' if supplier else 'out_invoice', 'partner_id': self.partner.id,
            'journal_id': self.journals['purchase' if supplier else 'sale'].id,
            'invoice_date': fields.Date.today(), 'advance_document': advance,
            'invoice_line_ids': [Command.create({
                'name': 'Advance' if advance else 'Final purchase/sale', 'account_id': account.id,
                'product_id': self.advance_product.id if advance else False,
                'quantity': 1, 'price_unit': amount, 'tax_ids': [Command.set(tax.ids if tax else [])],
            })],
        })
        return move

    def _pay(self, invoice, amount=None):
        invoice.with_context(bypass_risk=True).action_post()
        amount = invoice.amount_total if amount is None else amount
        supplier = invoice.move_type == 'in_invoice'
        trade = self.accounts['payable' if supplier else 'receivable']
        payment = self.env['account.move'].create({
            'journal_id': self.journals['payment'].id,
            'line_ids': [
                Command.create({'account_id': trade.id, 'partner_id': self.partner.id,
                                'debit': amount if supplier else 0, 'credit': 0 if supplier else amount}),
                Command.create({'account_id': self.accounts['expense'].id,
                                'credit': amount if supplier else 0, 'debit': 0 if supplier else amount}),
            ],
        })
        payment.action_post()
        (payment | invoice).line_ids.filtered(lambda line: line.account_id == trade and not line.reconciled).reconcile()
        return payment

    def _apply(self, source, target, amount, mode='deduction'):
        wizard = self.env['account.advance.apply'].create({
            'invoice_id': target.id, 'source_id': source.id, 'amount': amount, 'mode': mode,
        })
        action = wizard.action_apply()
        return self.env['account.move'].browse(action['res_id'])

    def test_unpaid_advance_is_not_available(self):
        source = self._invoice(300, advance=True)
        source.with_context(bypass_risk=True).action_post()
        self.assertEqual(source.advance_available, 0)
        with self.assertRaises(UserError):
            self._apply(source, self._invoice(800), 100)

    def test_partial_payment_limits_available_balance(self):
        source = self._invoice(300, supplier=True, advance=True)
        self._pay(source, 100)
        self.assertEqual(source.advance_paid, 100)
        self.assertEqual(source.advance_available, 100)
        target = self._invoice(800, supplier=True)
        self._apply(source, target, 20)
        self.assertEqual(source.advance_reserved, 20)
        self.assertEqual(source.advance_available, 80)
        target.action_post()
        self.assertEqual(source.advance_applied, 20)
        self.assertEqual(source.advance_reserved, 0)
        self.assertEqual(target.amount_residual, 780)

    def test_customer_advance_reduces_risk_once(self):
        source = self._invoice(300, advance=True)
        self._pay(source)
        self.assertEqual(self.partner.customer_advance_available, 300)
        self.assertEqual(self.partner.advance_purchase_power, 1300)
        target = self._invoice(600)
        self._apply(source, target, 300)
        self.assertEqual(source.advance_available, 0)
        target.action_post()
        self.assertEqual(target.state, 'posted')
        self.assertEqual(target.amount_residual, 300)
        self.assertEqual(self.partner.risk_gross_total, 300)
        self.assertEqual(self.partner.risk_total, 300)
        self.assertEqual(self.partner.advance_purchase_power, 700)
        self.assertEqual(target.advance_source_ids, source)
        self.assertEqual(source.advance_final_ids, target)

    def test_supplier_advance_does_not_increase_customer_credit(self):
        source = self._invoice(400, supplier=True, advance=True)
        self._pay(source)
        self.assertEqual(self.partner.supplier_advance_available, 400)
        self.assertEqual(self.partner.customer_advance_available, 0)
        self.assertEqual(self.partner.advance_purchase_power, 1000)

    def test_cancellation_releases_balance_without_cash_movement(self):
        source = self._invoice(300, advance=True)
        payment = self._pay(source)
        target = self._invoice(600)
        self._apply(source, target, 300)
        target.action_post()
        with self.assertRaises(UserError):
            source.button_draft()
        target.button_draft()
        target.button_cancel()
        self.assertEqual(source.advance_available, 300)
        self.assertEqual(source.advance_applied, 0)
        self.assertEqual(payment.state, 'posted')
        self.assertEqual(source.amount_residual, 0)

    def test_taxed_deduction_clears_advance_account(self):
        group = self.env['account.tax.group'].create({'name': 'Advance VAT', 'company_id': self.company.id, 'country_id': self.env.ref('base.us').id})
        tax = self.env['account.tax'].create({
            'name': 'Advance purchase VAT', 'type_tax_use': 'purchase', 'amount': 16,
            'amount_type': 'percent', 'company_id': self.company.id, 'tax_group_id': group.id,
            'country_id': self.env.ref('base.us').id,
        })
        (tax.invoice_repartition_line_ids | tax.refund_repartition_line_ids).filtered(
            lambda line: line.repartition_type == 'tax').account_id = self.accounts['vat']
        source = self._invoice(100, supplier=True, advance=True, tax=tax)
        self._pay(source)
        self.assertEqual(source.amount_total, 116)
        target = self._invoice(200, supplier=True, tax=tax)
        self._apply(source, target, 116)
        target.action_post()
        self.assertEqual(target.amount_total, 116)
        self.assertEqual(target.amount_tax, 16)
        self.assertEqual(sum((source | target).line_ids.filtered(
            lambda line: line.account_id == self.accounts['supplier']).mapped('balance')), 0)

    def test_application_credit_note_settles_final_bill(self):
        source = self._invoice(300, supplier=True, advance=True)
        self._pay(source)
        target = self._invoice(1000, supplier=True)
        target.action_post()
        credit = self._apply(source, target, 300, mode='credit_note')
        self.assertEqual(credit.state, 'draft')
        self.assertEqual(credit.advance_target_id, target)
        credit.action_post()
        self.assertEqual(target.amount_residual, 700)
        self.assertEqual(credit.amount_residual, 0)
        self.assertEqual(source.advance_available, 0)

    def test_pending_advance_offsets_prospective_risk(self):
        source = self._invoice(300, advance=True)
        self._pay(source)
        invoice = self._invoice(1200)
        self.assertFalse(invoice.risk_exception_msg())
        invoice.invoice_line_ids.price_unit = 1400
        self.assertTrue(invoice.risk_exception_msg())

    def test_wrong_operation_cannot_apply_supplier_advance_to_customer(self):
        source = self._invoice(300, supplier=True, advance=True)
        self._pay(source)
        with self.assertRaises(UserError):
            self._apply(source, self._invoice(800), 100)

    def test_purchase_advance_is_linked_without_invoicing_goods(self):
        product = self.env['product.product'].create({
            'name': 'Advance purchase goods', 'default_code': 'TEST-AD-GOODS', 'type': 'consu',
            'supplier_taxes_id': [Command.clear()],
        })
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'order_line': [Command.create({'product_id': product.id, 'product_qty': 10, 'price_unit': 100})],
        })
        order.button_confirm()
        action = self.env['purchase.advance.invoice'].create({
            'purchase_id': order.id, 'amount_type': 'percentage', 'percentage': 30,
            'reference': 'Supplier advance document',
        }).action_create()
        invoice = self.env['account.move'].browse(action['res_id'])
        self.assertEqual(invoice.amount_total, 300)
        self.assertEqual(invoice.state, 'draft')
        self.assertEqual(order.advance_invoice_ids, invoice)
        self.assertEqual(order.order_line.qty_invoiced, 0)

    def _native_sale_advance(self):
        product = self.env['product.product'].create({
            'name': 'Advance sale goods', 'default_code': 'TEST-AD-SALE', 'type': 'consu',
            'invoice_policy': 'order', 'taxes_id': [Command.clear()],
            'property_account_income_id': self.accounts['income'].id,
        })
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 10, 'price_unit': 100})],
        })
        order.action_confirm()
        self.env['sale.advance.payment.inv'].create({
            'sale_order_ids': [Command.set(order.ids)], 'advance_payment_method': 'fixed', 'fixed_amount': 300,
        }).create_invoices()
        source = order.invoice_ids
        self._pay(source)
        return order, source

    def test_native_sales_advance_links_to_final_invoice(self):
        order, source = self._native_sale_advance()
        target = order._create_invoices(final=True)
        self.assertEqual(target.advance_source_ids, source)
        target.with_context(bypass_risk=True).action_post()
        self.assertEqual(target.amount_total, 700)
        self.assertEqual(source.advance_available, 0)

    def test_reservations_cannot_spend_same_advance_twice(self):
        source = self._invoice(300, advance=True)
        self._pay(source)
        first = self._invoice(500)
        self._apply(source, first, 250)
        self.assertEqual(source.advance_available, 50)
        with self.assertRaises(UserError):
            self._apply(source, self._invoice(500), 100)
        first.button_cancel()
        self.assertEqual(source.advance_available, 300)

    def test_cannot_unreconcile_payment_of_applied_advance(self):
        source = self._invoice(300, advance=True)
        payment = self._pay(source)
        self._apply(source, self._invoice(500), 100)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            payment.line_ids.filtered(lambda line: line.account_id == self.accounts['receivable']).remove_move_reconcile()
        self.assertEqual(source.advance_paid, 300)

    def _pos_order(self, amount, source=None, pay_later=False, partner=True):
        config = self.env['pos.config'].create({'name': 'Advance POS test', 'company_id': self.company.id})
        method = self.env['pos.payment.method'].create({'name': 'Advance credit test', 'journal_id': False, 'type': 'pay_later'}) if pay_later else None
        return self.env['pos.order'].new({
            'config_id': config.id, 'company_id': self.company.id,
            'partner_id': self.partner.id if partner else False, 'to_invoice': True,
            'amount_total': amount, 'amount_paid': amount,
            'lines': [Command.create({'product_id': self.advance_product.id, 'qty': 1,
                                      'price_unit': amount, 'price_subtotal': amount, 'price_subtotal_incl': amount,
                                      'advance_source_ref': source.id if source else 0})],
            'payment_ids': [Command.create({'payment_method_id': method.id, 'amount': amount})] if method else [],
        })

    def test_pos_requires_customer_and_real_payment_for_advance(self):
        with self.assertRaises(UserError):
            self._pos_order(100, partner=False)._check_pos_advances()
        with self.assertRaises(UserError):
            self._pos_order(100, pay_later=True)._check_pos_advances()
        self._pos_order(100)._check_pos_advances()

    def test_pos_invoice_uses_advance_liability(self):
        order = self._pos_order(100)
        values = order._prepare_invoice_lines('out_invoice')
        product_line = next(command[2] for command in values if command[2].get('product_id'))
        self.assertEqual(product_line['account_id'], self.accounts['customer'].id)

    def test_pos_draft_sync_restores_required_advance_invoice(self):
        order = self._financed_pos_order(paid=100, pending=0, flow='standard')
        order.lines.product_id = self.advance_product
        order.payment_ids.filtered(lambda p: p.payment_method_id.type == 'pay_later').unlink()
        order.to_invoice = False
        order._process_saved_order(draft=True)
        self.assertTrue(order.to_invoice)
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.account_move)
        order._check_pos_advances()

    def test_pos_cash_advance_sync_generates_and_pays_invoice_without_ui_flag(self):
        cash_journal = self.env['account.journal'].create({
            'name': 'Advance cash', 'code': 'ACSH', 'type': 'cash',
            'company_id': self.company.id,
        })
        cash = self.env['pos.payment.method'].create({
            'name': 'Cash advance', 'journal_id': cash_journal.id,
            'company_id': self.company.id,
        })
        order = self._financed_pos_order(paid=100, pending=0, flow='standard', receipt_method=cash)
        order.lines.product_id = self.advance_product
        order.payment_ids.filtered(lambda p: p.payment_method_id.type == 'pay_later').unlink()
        order.to_invoice = False
        order.with_context(generate_pdf=False)._process_saved_order(draft=False)
        self.assertTrue(order.to_invoice)
        self.assertEqual(order.account_move.state, 'posted')
        self.assertTrue(order.account_move.is_advance_invoice)
        self.assertEqual(order.account_move.amount_total, 100)
        self.assertEqual(order.account_move.amount_residual, 0)
        self.assertEqual(order.account_move.advance_available, 100)

    def test_pos_loader_preserves_native_configuration_and_company(self):
        order = self._pos_order(100)
        config = order.config_id
        data = config._load_pos_data_search_read({}, config)
        self.assertEqual(len(data), 1)
        self.assertIn('use_pricelist', data[0])
        self.assertEqual(data[0]['company_id'], self.company.id)
        self.assertEqual(data[0]['advance_product_id'], self.advance_product.id)
        self.assertTrue(data[0]['advance_risk_enabled'])
        companies = self.env['res.company']._load_pos_data_search_read({'pos.config': data}, config)
        company = next(value for value in companies if value['id'] == self.company.id)
        self.assertIn('tax_calculation_rounding_method', company)

    def test_pos_purchase_consumes_customer_funds_without_creating_debt(self):
        source = self._invoice(5000, advance=True)
        self._pay(source)
        order = self._financed_pos_order(paid=1500, pending=0, flow='standard')
        order.payment_ids.unlink()
        order.write({
            'amount_total': 0, 'amount_paid': 0,
            'lines': [Command.create({
                'product_id': self.advance_product.id, 'qty': 1, 'price_unit': -1500,
                'price_subtotal': -1500, 'price_subtotal_incl': -1500,
                'advance_source_ref': source.id,
            })],
        })
        order.with_context(generate_pdf=False)._process_saved_order(draft=False)
        self.assertEqual(order.account_move.state, 'posted')
        self.assertEqual(order.account_move.amount_total, 0)
        self.assertEqual(order.account_move.amount_residual, 0)
        self.assertEqual(source.advance_available, 3500)
        data = self.env['res.partner']._load_pos_data_read(self.partner, order.config_id)[0]
        self.assertEqual(data['customer_advance_available'], 3500)

    def test_financial_risk_converts_company_residual_correctly(self):
        other = self.env.ref('base.MXN') if self.company.currency_id != self.env.ref('base.MXN') else self.env.ref('base.USD')
        other.active = True
        # The amount_residual is always in company currency, even when the line uses another currency.
        self.assertEqual(self.partner._get_amount_in_risk_currency(
            other, 1700, 100, self.accounts['receivable'], self.company), 100)

    def test_fiscal_application_requires_origin_uuid(self):
        source = self._invoice(300, advance=True)
        source.advance_fiscal_kind = 'fiscal'
        self._pay(source)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._apply(source, self._invoice(500), 100)
        self.assertEqual(source.advance_reserved, 0)

    def test_fiscal_application_links_multiple_uuids_without_stamping(self):
        sources = self.env['account.move']
        uuids = ['12345678-1234-1234-1234-123456789001', '12345678-1234-1234-1234-123456789002']
        for amount, uuid in zip((100, 200), uuids):
            source = self._invoice(amount, advance=True)
            source.advance_fiscal_kind = 'fiscal'
            self._pay(source)
            # Simulate already received/stamped origin UUIDs, without calling a PAC.
            source.l10n_mx_edi_cfdi_uuid = uuid
            sources |= source
        target = self._invoice(1000)
        self._apply(sources[0], target, 100)
        self._apply(sources[1], target, 200)
        self.assertEqual(target.l10n_mx_edi_cfdi_origin, '07|' + ','.join(uuids))
        target.with_context(bypass_risk=True).action_post()
        self.assertEqual(target.amount_total, 700)
        self.assertEqual(target.advance_source_ids, sources)
        self.assertEqual(sum(sources.mapped('advance_available')), 0)

    def test_posted_advance_metadata_and_application_cannot_be_retargeted(self):
        source = self._invoice(300, advance=True)
        self._pay(source)
        with self.assertRaises(UserError):
            source.advance_fiscal_kind = 'fiscal'
        target = self._invoice(500)
        self._apply(source, target, 100)
        target.action_post()
        with self.assertRaises(UserError):
            target.invoice_line_ids.filtered('advance_source_id').advance_source_id = False

    def test_configuration_reuses_selected_accounts_and_product(self):
        self.env.user.group_ids |= self.env.ref('account.group_account_manager')
        accounts = self.company.downpayment_account_id | self.company.supplier_advance_account_id
        self.company.action_setup_advance_accounts()
        self.company.action_setup_advance_accounts()
        self.assertEqual(self.company.downpayment_account_id | self.company.supplier_advance_account_id, accounts)
        self.assertEqual(self.company.advance_product_id, self.advance_product)

    def test_pos_rejects_finalized_advance_if_invoice_failed(self):
        order = self._pos_order(100)
        order.state = 'paid'
        # Emulate the persisted ID returned by native synchronization. NewId is
        # intentionally falsey and cannot be round-tripped through browse().
        with patch.object(ExistingPosOrder, '_process_order', return_value=123), \
                patch.object(type(self.env['pos.order']), 'browse', return_value=order):
            with self.assertRaises(UserError):
                self.env['pos.order']._process_order({}, False)

    def test_pos_accepts_finalized_advance_with_posted_invoice(self):
        source = self._invoice(100, advance=True)
        self._pay(source)
        order = self._pos_order(100)
        order.state = 'paid'
        order.account_move = source
        with patch.object(ExistingPosOrder, '_process_order', return_value=123), \
                patch.object(type(self.env['pos.order']), 'browse', return_value=order):
            self.assertEqual(self.env['pos.order']._process_order({}, False), 123)

    def test_historical_native_deduction_is_not_available_again(self):
        self.company.advance_risk_enabled = False
        order, source = self._native_sale_advance()
        target = order._create_invoices(final=True)
        self.assertFalse(target.invoice_line_ids.advance_source_id)
        target.with_context(bypass_risk=True).action_post()
        self.company.advance_risk_enabled = True
        self.assertEqual(source.advance_available, 0)
        self.assertEqual(source.advance_applied, 300)
        self.assertEqual(self.partner.customer_advance_available, 0)
        self.assertEqual(source.advance_final_ids, target)
        self.assertEqual(target.advance_source_ids, source)

    def test_legacy_advance_in_income_account_does_not_create_credit(self):
        self.company.advance_risk_enabled = False
        self.company.advance_product_id = False
        self.company.downpayment_account_id = False
        _order, source = self._native_sale_advance()
        self.assertEqual(source.invoice_line_ids.account_id, self.accounts['income'])
        self.company.downpayment_account_id = self.accounts['customer']
        self.company.advance_product_id = self.advance_product
        self.company.advance_risk_enabled = True
        self.assertEqual(source.advance_available, 0)
        self.assertEqual(self.partner.customer_advance_available, 0)

    def test_native_pos_deposit_links_to_sales_final_without_double_invoice(self):
        sale, first = self._native_sale_advance()
        product = self.env['product.product'].create({
            'name': 'Native POS down payment', 'type': 'service', 'available_in_pos': True,
            'property_account_income_id': self.accounts['income'].id,
            'taxes_id': [Command.clear()],
        })
        config = self.env['pos.config'].create({
            'name': 'Native advance POS', 'company_id': self.company.id,
            'down_payment_product_id': product.id,
        })
        session = self.env['pos.session'].create({'config_id': config.id, 'user_id': self.env.uid})
        pos = self.env['pos.order'].create({
            'session_id': session.id, 'partner_id': self.partner.id, 'to_invoice': True,
            'amount_total': 200, 'amount_tax': 0, 'amount_paid': 200, 'amount_return': 0,
            'lines': [Command.create({'product_id': product.id, 'qty': 1, 'price_unit': 200,
                                      'price_subtotal': 200, 'price_subtotal_incl': 200,
                                      'sale_order_origin_id': sale.id})],
        })
        pos._check_pos_advances()
        values = pos._prepare_invoice_lines('out_invoice')
        self.assertEqual(next(value[2] for value in values if value[2].get('product_id'))['account_id'],
                         self.accounts['customer'].id)
        invoice_values = pos._prepare_invoice_vals()
        invoice_values['journal_id'] = self.journals['sale'].id
        source = self.env['account.move'].create(invoice_values)
        self._pay(source)
        pos.account_move = source
        self.assertTrue(source.invoice_line_ids.filtered(lambda line: line.display_type == 'product').is_downpayment)
        base_lines = pos.lines._prepare_tax_base_line_values()
        Tax = self.env['account.tax']
        Tax._add_tax_details_in_base_lines(base_lines, self.company)
        Tax._round_base_lines_tax_details(base_lines, self.company)
        sale._create_down_payment_section_line_if_needed()
        sale._create_down_payment_lines_from_base_lines(base_lines)
        pos.state = 'paid'
        self.assertTrue(pos.lines.sale_order_line_id.is_downpayment)
        self.assertFalse(source.invoice_line_ids.sale_line_ids)
        target = sale._create_invoices(final=True)
        self.assertEqual(target.advance_source_ids, first | source)
        self.assertEqual(target.amount_total, 500)
        target.with_context(bypass_risk=True).action_post()
        self.assertEqual(source.advance_available, 0)
        self.assertEqual(source.advance_applied, 200)
        self.assertEqual(source.advance_final_ids, target)

    def test_native_pos_deduction_finds_origin_and_uses_liability(self):
        sale, source = self._native_sale_advance()
        order = self._pos_order(-100)
        order.config_id.down_payment_product_id = self.advance_product
        order.lines.write({'sale_order_origin_id': sale.id,
                           'sale_order_line_id': sale.order_line.filtered(lambda line: line.is_downpayment and not line.display_type).id})
        # A native deduction has no custom source reference in its UI payload.
        self.assertFalse(order.lines.advance_source_ref)
        self.assertEqual(order.lines._accounting_advance_source(), source)
        values = order._prepare_invoice_lines('out_invoice')
        line = next(value[2] for value in values if value[2].get('product_id'))
        self.assertEqual(line['advance_source_id'], source.id)
        self.assertEqual(line['account_id'], self.accounts['customer'].id)

    def _financed_pos_order(self, paid=300, pending=700, flow='ppd_partial', receipt_method=None):
        self.env.user.group_ids |= self.env.ref('point_of_sale.group_pos_manager')
        product = self.env['product.product'].create({
            'name': 'Financed POS goods', 'type': 'service',
            'property_account_income_id': self.accounts['income'].id,
            'taxes_id': [Command.clear()],
        })
        config = self.env['pos.config'].create({
            'name': 'Deferred payment POS', 'company_id': self.company.id,
            'invoice_journal_id': self.journals['sale'].id,
        })
        config.action_prepare_deferred_payments()
        self.journals['bank'].inbound_payment_method_line_ids.payment_account_id = self.accounts['outstanding']
        method = receipt_method or self.env['pos.payment.method'].create({
            'name': 'Actual bank receipt', 'journal_id': self.journals['bank'].id,
            'company_id': self.company.id, 'split_transactions': True,
        })
        config.payment_method_ids = [Command.link(method.id)]
        deferred = config.payment_method_ids.filtered(lambda value: value.type == 'pay_later')[:1]
        session = self.env['pos.session'].create({'config_id': config.id, 'user_id': self.env.uid})
        return self.env['pos.order'].create({
            'session_id': session.id, 'partner_id': self.partner.id, 'to_invoice': True,
            'amount_total': paid + pending, 'amount_tax': 0, 'amount_paid': paid + pending, 'amount_return': 0,
            'pos_payment_flow': flow,
            'lines': [Command.create({'product_id': product.id, 'qty': 1, 'price_unit': paid + pending,
                                      'price_subtotal': paid + pending, 'price_subtotal_incl': paid + pending})],
            'payment_ids': [Command.create({'payment_method_id': method.id, 'amount': paid}),
                            Command.create({'payment_method_id': deferred.id, 'amount': pending})],
        })

    def test_ppd_partial_permits_unapproved_customer_and_keeps_real_residual(self):
        self.partner.write({'is_credit': False, 'credit_limit': 0})
        order = self._financed_pos_order()
        order._check_pos_deferred_payments(check_risk=True)
        values = order._prepare_invoice_vals()
        self.assertEqual(values['l10n_mx_edi_payment_policy'], 'PPD')
        self.assertEqual(values['invoice_payment_term_id'], self.env.ref('entintados_pdv.payment_term_pos_ppd').id)
        order.action_pos_order_paid()
        invoice = order.with_context(generate_pdf=False)._generate_pos_order_invoice()
        self.assertEqual(invoice.amount_total, 1000)
        self.assertEqual(invoice.amount_residual, 700)
        self.assertEqual(invoice.pos_financing_flow, 'ppd_partial')
        self.assertFalse(order.payment_ids.filtered(lambda value: value.payment_method_id.type == 'pay_later').account_move_id)
        self.assertEqual(sum(order.payment_ids.filtered('account_move_id').mapped('amount')), 300)
        self.assertEqual(self.partner._pos_credit_exposure(), 700)
        # Native invoice collection uses zero-quantity settlement lines and a
        # negative account entry, never a second product sale.
        bank_method = order.payment_ids.filtered(lambda value: value.payment_method_id.type != 'pay_later').payment_method_id
        deferred_method = order.payment_ids.filtered(lambda value: value.payment_method_id.type == 'pay_later').payment_method_id
        collection = self.env['pos.order'].create({
            'session_id': order.session_id.id, 'partner_id': self.partner.id, 'to_invoice': False,
            'amount_total': 0, 'amount_tax': 0, 'amount_paid': 0, 'amount_return': 0,
            'lines': [Command.create({'product_id': order.config_id.settle_invoice_product_id.id,
                                      'qty': 0, 'price_unit': 700, 'price_subtotal': 0, 'price_subtotal_incl': 0,
                                      'settled_invoice_id': invoice.id})],
            'payment_ids': [Command.create({'payment_method_id': bank_method.id, 'amount': 700}),
                            Command.create({'payment_method_id': deferred_method.id, 'amount': -700})],
        })
        collection.action_pos_order_paid()
        self.assertEqual(invoice.pos_amount_unsettled, 0)
        order.session_id._validate_session()
        self.assertEqual(invoice.amount_residual, 0)

    def test_ppd_requires_real_initial_payment(self):
        order = self._financed_pos_order(paid=0, pending=1000)
        with self.assertRaises(UserError):
            order._check_pos_deferred_payments(check_risk=True)

    def test_native_pos_unset_payment_flow_is_normalized(self):
        order = self._financed_pos_order(paid=1000, pending=0, flow=False)
        self.assertEqual(order.pos_payment_flow, 'standard')
        order.pos_payment_flow = False
        self.assertEqual(order.pos_payment_flow, 'standard')

    def test_credit_requires_authorization_and_limit(self):
        order = self._financed_pos_order(paid=0, pending=1000, flow='credit')
        self.partner.is_credit = False
        with self.assertRaises(UserError):
            order._check_pos_deferred_payments(check_risk=True)
        self.partner.write({'is_credit': True, 'credit_limit': 500})
        with self.assertRaises(UserError):
            order._check_pos_deferred_payments(check_risk=True)
        self.partner.credit_limit = 1000
        order._check_pos_deferred_payments(check_risk=True)
        order.state = 'paid'
        self.partner.is_credit = False
        with self.assertRaises(UserError):
            order.action_pos_order_paid()

    def test_deferred_sales_require_invoice_even_if_ui_flag_is_removed(self):
        order = self._financed_pos_order()
        order.to_invoice = False
        with self.assertRaises(UserError):
            order._check_pos_deferred_payments(check_risk=True)

    def test_credit_accounts_for_previous_ppd_without_optional_risk_categories(self):
        self.partner.write({'risk_invoice_open_include': False, 'risk_invoice_unpaid_include': False})
        invoice = self._invoice(700)
        invoice.pos_financing_flow = 'ppd_partial'
        invoice.with_context(bypass_risk=True).action_post()
        order = self._financed_pos_order(paid=0, pending=400, flow='credit')
        with self.assertRaises(UserError):
            order._check_pos_deferred_payments(check_risk=True)
