from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVendorCreditGroup(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.payable = cls.env['account.account'].create({
            'name': 'Credit test payable', 'code': 'VCRPAY',
            'account_type': 'liability_payable', 'reconcile': True,
            'company_ids': [Command.set(cls.company.ids)],
        })
        cls.expense = cls.env['account.account'].create({
            'name': 'Credit test expense', 'code': 'VCREXP',
            'account_type': 'expense',
            'company_ids': [Command.set(cls.company.ids)],
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Credit test supplier', 'phone': '5551234567',
            'is_supplier': True, 'distributed_notes': True,
            'property_account_payable_id': cls.payable.id,
        })
        cls.journal = cls.env['account.journal'].create({
            'name': 'Credit test purchases', 'code': 'VCRP', 'type': 'purchase',
            'company_id': cls.company.id,
        })
        cls.payment_journal = cls.env['account.journal'].create({
            'name': 'Credit test payments', 'code': 'VCRM', 'type': 'general',
            'company_id': cls.company.id,
        })

    def _invoice(self, registered_payment=False, payment_amount=40, tax=None, currency=None, total=100, product=None):
        invoice = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id,
            'journal_id': self.journal.id, 'invoice_date': fields.Date.today(),
            'currency_id': (currency or self.company.currency_id).id,
            'invoice_line_ids': [Command.create({
                'name': 'Purchase', 'account_id': self.expense.id,
                'product_id': product.id if product else False,
                'quantity': 10 if product else 1, 'price_unit': total / 10 if product else total,
                'tax_ids': [Command.set(tax.ids if tax else [])],
            })],
        })
        invoice.action_post()
        if payment_amount is None:
            return invoice
        if registered_payment:
            outstanding = self.env['account.account'].search([('code', '=', 'VCROUT'), ('company_ids', 'in', self.company.id)], limit=1)
            outstanding = outstanding or self.env['account.account'].create({
                'name': 'Credit test outstanding', 'code': 'VCROUT',
                'account_type': 'asset_current', 'reconcile': True,
                'company_ids': [Command.set(self.company.ids)],
            })
            bank = self.env['account.journal'].search([('code', '=', 'VCRB'), ('company_id', '=', self.company.id)], limit=1)
            bank = bank or self.env['account.journal'].create({
                'name': 'Credit test bank', 'code': 'VCRB', 'type': 'bank',
                'company_id': self.company.id,
            })
            method = bank.outbound_payment_method_line_ids[:1]
            method.payment_account_id = outstanding
            registered = self.env['account.payment'].create({
                'journal_id': bank.id, 'payment_method_line_id': method.id,
                'partner_id': self.partner.id, 'partner_type': 'supplier',
                'payment_type': 'outbound', 'amount': payment_amount,
                'currency_id': invoice.currency_id.id,
            })
            registered.action_post()
            payment = registered.move_id
            self.assertFalse(registered.is_matched)
        else:
            payment = self.env['account.move'].create({
                'journal_id': self.payment_journal.id,
                'line_ids': [
                    Command.create({'account_id': self.payable.id,
                                    'partner_id': self.partner.id, 'debit': payment_amount}),
                    Command.create({'account_id': self.expense.id, 'credit': payment_amount}),
                ],
            })
            payment.action_post()
        (invoice | payment).line_ids.filtered(
            lambda line: line.account_id == self.payable
        ).reconcile()
        if payment_amount < 100:
            self.assertEqual(invoice.payment_state, 'partial')
        return invoice

    def _group(self, invoices, amount=60):
        return self.env['vendor.credit.group'].create({
            'partner_id': self.partner.id, 'company_id': self.company.id, 'supplier_reference': 'PROV-QA-001',
            'currency_id': invoices.currency_id.id,
            'journal_id': self.journal.id, 'reason': 'Credit test',
            'reason_id': self.env.ref('entintados_pdv.vendor_credit_reason_bonus').id,
            'line_ids': [Command.create({'invoice_id': invoice.id, 'amount': amount})
                         for invoice in invoices],
        })

    def test_individual_credits_paid_and_no_duplicates(self):
        invoices = self._invoice() | self._invoice()
        group = self._group(invoices)
        action = group.action_create_credit()
        self.assertEqual(group.credit_count, 2)
        self.assertEqual(action['view_mode'], 'list,form')
        for invoice in invoices:
            self.assertEqual(invoice.vendor_credit_note_count, 1)
            credit = invoice.vendor_credit_note_ids
            self.assertEqual(credit.vendor_credit_source_ids, invoice)
            self.assertEqual(credit.vendor_credit_source_count, 1)
            self.assertEqual(credit.state, 'posted')
            self.assertEqual(invoice.payment_state, 'paid')
            self.assertEqual(credit.payment_state, 'paid')
            self.assertEqual(invoice.amount_residual, 0)
            self.assertEqual(credit.amount_residual, 0)
        credits = group.credit_ids
        group.action_create_credit()
        self.assertEqual(group.credit_ids, credits)

    def test_manual_credit_rebalances_payment_and_settles_invoice(self):
        invoice = self._invoice(registered_payment=True)
        payment = invoice.reconciled_payment_ids
        group = self._group(invoice, amount=20)
        group.action_create_credit()
        self.assertEqual(invoice.amount_residual, 0)
        self.assertEqual(invoice.payment_state, 'paid')
        self.assertEqual(payment.amount, 80)
        self.assertEqual(group.credit_ids.payment_state, 'paid')
        self.assertEqual(group.credit_ids.amount_residual, 0)

    def test_cancel_restores_balance_and_relations(self):
        invoices = self._invoice() | self._invoice()
        group = self._group(invoices)
        group.action_create_credit()
        credits = group.credit_ids
        group.action_cancel()
        self.assertEqual(group.state, 'cancel')
        self.assertEqual(group.credit_count, 0)
        for invoice in invoices:
            self.assertEqual(invoice.amount_residual, 60)
            self.assertEqual(invoice.payment_state, 'partial')
            self.assertEqual(invoice.vendor_credit_note_count, 0)
        for credit in credits:
            self.assertEqual(credit.state, 'cancel')
            self.assertFalse(credit.vendor_credit_source_ids)

    def test_reset_cancelled_group_edit_and_regenerate(self):
        invoices = self._invoice(registered_payment=True) | self._invoice(registered_payment=True)
        group = self._group(invoices)
        group.action_create_credit()
        old_credits = group.credit_ids
        selected_lines = group.line_ids
        group.action_cancel()
        with self.assertRaises(UserError):
            selected_lines[0].write({'amount': 20})
        group.action_draft()
        self.assertEqual(group.state, 'draft')
        self.assertEqual(group.line_ids, selected_lines)
        self.assertEqual(group.line_ids.invoice_id, invoices)
        selected_lines[0].write({'amount': 20})
        selected_lines[1].write({'selected': False})
        group.action_create_credit()
        self.assertEqual(group.credit_count, 1)
        self.assertFalse(group.credit_ids & old_credits)
        self.assertEqual(group.credit_ids.amount_total, 20)
        self.assertEqual(selected_lines[0].invoice_id.amount_residual, 0)
        self.assertEqual(group.credit_ids.payment_state, 'paid')
        for credit in old_credits:
            self.assertEqual(credit.state, 'cancel')

    def test_reset_generated_group_is_rejected(self):
        group = self._group(self._invoice())
        group.action_create_credit()
        with self.assertRaises(UserError):
            group.action_draft()
        self.assertEqual(group.state, 'generated')
        self.assertEqual(group.credit_ids.state, 'posted')

    def test_registered_payment_and_credit_settle_invoice(self):
        # Reproduce Enterprise's in_payment state while the bank is unmatched.
        with patch.object(type(self.env['account.move']),
                          '_get_invoice_in_payment_state', return_value='in_payment'):
            invoice = self._invoice(registered_payment=True)
            payments = invoice.reconciled_payment_ids
            group = self._group(invoice)
            group.action_create_credit()
            self.assertEqual(invoice.amount_residual, 0)
            self.assertEqual(invoice.payment_state, 'paid')
            self.assertEqual(group.credit_ids.payment_state, 'paid')
            self.assertFalse(payments.is_matched)
            # Merely having a related credit is insufficient: it must be applied.
            credit = group.credit_ids
            credit.vendor_credit_source_ids = [Command.clear()]
            self.assertEqual(invoice.payment_state, 'in_payment')
            credit.vendor_credit_source_ids = [Command.set(invoice.ids)]
            self.assertEqual(invoice.payment_state, 'paid')
            group.action_cancel()
            self.assertEqual(invoice.amount_residual, 60)
            self.assertEqual(invoice.payment_state, 'partial')

    def test_unrelated_full_payment_keeps_bank_pending_state(self):
        with patch.object(type(self.env['account.move']),
                          '_get_invoice_in_payment_state', return_value='in_payment'):
            invoice = self._invoice(registered_payment=True, payment_amount=100)
            self.assertEqual(invoice.amount_residual, 0)
            self.assertEqual(invoice.payment_state, 'in_payment')

    def test_purchase_journal_is_automatic(self):
        self.journal.sequence = 0
        group = self.env['vendor.credit.group'].create({
            'partner_id': self.partner.id, 'company_id': self.company.id,
            'currency_id': self.company.currency_id.id, 'reason': 'Automatic purchase journal', 'supplier_reference': 'QA-JOURNAL-001',
        })
        self.assertEqual(group.journal_id, self.journal)
        self.assertEqual(group.journal_id.type, 'purchase')

    def test_purchase_journal_follows_company(self):
        company = self.env['res.company'].create({'name': 'Other vendor credit company'})
        journal = self.env['account.journal'].create({
            'name': 'Other company purchases', 'code': 'VCO', 'type': 'purchase', 'company_id': company.id,
        })
        group = self.env['vendor.credit.group'].with_context(allowed_company_ids=[self.company.id, company.id]).create({
            'partner_id': self.partner.id, 'company_id': company.id,
            'currency_id': company.currency_id.id, 'reason': 'Other company journal', 'supplier_reference': 'QA-JOURNAL-002',
        })
        self.assertEqual(group.journal_id, journal)

    def test_existing_purchase_journal_is_preserved(self):
        group = self._group(self._invoice())
        group._onchange_scope()
        self.assertEqual(group.journal_id, self.journal)

    def test_single_selection_action_selects_missing_or_deselects_all(self):
        group = self._group(self._invoice() | self._invoice())
        amounts = group.line_ids.mapped('amount')
        self.assertEqual(group.selection_toggle_label, 'Deseleccionar todas')
        group.action_toggle_invoice_selection()
        self.assertFalse(any(group.line_ids.mapped('selected')))
        self.assertEqual(group.amount_total, 0)
        self.assertEqual(group.invoice_count, 0)
        self.assertEqual(group.selection_toggle_label, 'Seleccionar todas')
        group.line_ids[0].selected = True
        self.assertEqual(group.selection_toggle_label, 'Seleccionar todas')
        group.action_toggle_invoice_selection()
        self.assertTrue(all(group.line_ids.mapped('selected')))
        self.assertEqual(group.selection_toggle_label, 'Deseleccionar todas')
        self.assertEqual(group.invoice_count, 2)
        self.assertEqual(group.amount_total, sum(amounts))
        self.assertEqual(group.line_ids.mapped('amount'), amounts)

    def test_selection_label_updates_when_editing_a_single_checkbox(self):
        group = self._group(self._invoice() | self._invoice())
        with Form(group) as form:
            self.assertEqual(form.selection_toggle_label, 'Deseleccionar todas')
            with form.line_ids.edit(0) as line:
                line.selected = False
            self.assertEqual(form.selection_toggle_label, 'Seleccionar todas')

    def test_selection_action_does_not_change_generated_or_cancelled_groups(self):
        group = self._group(self._invoice())
        group.action_create_credit()
        with self.assertRaises(UserError):
            group.action_toggle_invoice_selection()
        group.action_cancel()
        with self.assertRaises(UserError):
            group.action_toggle_invoice_selection()

    def test_only_one_selection_button_above_invoice_lines(self):
        view = self.env.ref('entintados_pdv.vendor_credit_wizard_form')._get_combined_arch()
        buttons = view.xpath('//button[@name="action_toggle_invoice_selection"]')
        self.assertEqual(len(buttons), 1)
        self.assertTrue(buttons[0].xpath('./field[@name="selection_toggle_label"]'))
        self.assertTrue(buttons[0].getparent().xpath('following-sibling::field[@name="line_ids"]'))

    def _adjustment(self, group, amount, **extra):
        return self.env['vendor.credit.group.adjustment'].with_context(default_group_id=group.id).create({
            'explanation': 'Cambio autorizado del importe',
            'line_ids': [Command.create({'source_line_id': line.id, 'amount': amount}) for line in group.line_ids],
            **extra,
        })

    def test_catalog_reason_is_copied_to_generated_credit(self):
        group = self._group(self._invoice())
        group.action_create_credit()
        self.assertEqual(group.reason, group.reason_id.name)
        self.assertEqual(group.credit_ids.ref, group.reason_id.name)

    def test_adjust_generated_credit_decreases_note_and_increases_payment(self):
        invoice = self._invoice(registered_payment=True)
        payment = invoice.reconciled_payment_ids
        group = self._group(invoice)
        group.action_create_credit()
        credit = group.credit_ids
        wizard = self._adjustment(group, 50)
        self.assertEqual(wizard.line_ids.payment_amount, 50)
        wizard.action_apply()
        self.assertEqual(group.credit_ids, credit)
        self.assertEqual(credit.amount_total, 50)
        self.assertEqual(payment.amount, 50)
        self.assertEqual(invoice.amount_residual, 0)
        self.assertEqual(invoice.payment_state, 'paid')
        self.assertEqual(payment.move_id.state, 'posted')
        self.assertEqual(sum(payment.move_id.line_ids.mapped('balance')), 0)
        self.assertEqual(group.adjustment_revision, 1)
        self.assertTrue(group.message_ids)

    def test_adjust_generated_credit_increases_note_and_decreases_payment(self):
        invoice = self._invoice(registered_payment=True)
        payment = invoice.reconciled_payment_ids
        group = self._group(invoice)
        group.action_create_credit()
        self._adjustment(group, 70).action_apply()
        self.assertEqual(payment.amount, 30)
        self.assertEqual(group.credit_ids.amount_total, 70)
        self.assertEqual(invoice.amount_residual, 0)
        self.assertEqual(group.line_ids.payment_id, payment)

    def test_total_amount_is_manually_distributed_and_settles_all_bills(self):
        invoices = self._invoice(registered_payment=True) | self._invoice(registered_payment=True)
        group = self._group(invoices)
        group.amount_total = 100
        self.assertEqual(group.line_ids.mapped('amount'), [50, 50])
        group.action_create_credit()
        self.assertEqual(sum(invoices.mapped('amount_residual')), 0)
        self.assertEqual(sorted(invoices.reconciled_payment_ids.mapped('amount')), [50, 50])

    def test_sent_payment_blocks_adjustment_without_changes(self):
        invoice = self._invoice(registered_payment=True)
        payment = invoice.reconciled_payment_ids
        group = self._group(invoice)
        group.action_create_credit()
        payment.is_sent = True
        with self.assertRaises(UserError), self.cr.savepoint():
            self._adjustment(group, 50).action_apply()
        self.assertEqual(payment.amount, 40)
        self.assertEqual(group.credit_ids.amount_total, 60)
        self.assertEqual(invoice.amount_residual, 0)

    def _match_bank_payment(self, payment, amount=None):
        suspense = self.env['account.account'].create({
            'name': 'Vendor credit bank suspense', 'code': 'VCRSUSP',
            'account_type': 'asset_current', 'reconcile': True,
            'company_ids': [Command.set(self.company.ids)],
        })
        payment.journal_id.suspense_account_id = suspense
        statement = self.env['account.bank.statement.line'].create({
            'journal_id': payment.journal_id.id, 'date': fields.Date.today(),
            'payment_ref': 'Vendor bank payment matched', 'amount': -(payment.amount if amount is None else amount),
            'partner_id': self.partner.id,
        })
        _, counterpart, _ = statement._seek_for_lines()
        counterpart.account_id = payment.outstanding_account_id
        (counterpart | payment.move_id.line_ids.filtered(
            lambda line: line.account_id == payment.outstanding_account_id)).reconcile()

    def test_bank_matched_payment_blocks_adjustment(self):
        invoice = self._invoice(registered_payment=True)
        payment = invoice.reconciled_payment_ids
        group = self._group(invoice)
        group.action_create_credit()
        self._match_bank_payment(payment)
        self.assertTrue(payment.is_matched)
        with self.assertRaises(UserError), self.cr.savepoint():
            self._adjustment(group, 50).action_apply()
        self.assertEqual(payment.amount, 40)
        self.assertEqual(group.credit_ids.amount_total, 60)

    def test_partly_bank_matched_payment_is_also_protected(self):
        invoice = self._invoice(registered_payment=True)
        payment = invoice.reconciled_payment_ids
        group = self._group(invoice)
        group.action_create_credit()
        self._match_bank_payment(payment, amount=20)
        self.assertFalse(payment.is_matched)
        with self.assertRaises(UserError), self.cr.savepoint():
            self._adjustment(group, 50).action_apply()
        self.assertEqual(payment.amount, 40)

    def test_stamped_credit_blocks_adjustment(self):
        invoice = self._invoice(registered_payment=True)
        group = self._group(invoice)
        group.action_create_credit()
        group.credit_ids.l10n_mx_edi_cfdi_state = 'sent'
        with self.assertRaises(UserError), self.cr.savepoint():
            self._adjustment(group, 50).action_apply()
        self.assertEqual(group.credit_ids.amount_total, 60)

    def test_stale_adjustment_cannot_overwrite_a_previous_adjustment(self):
        invoice = self._invoice(registered_payment=True)
        group = self._group(invoice)
        group.action_create_credit()
        first, stale = self._adjustment(group, 50), self._adjustment(group, 55)
        first.action_apply()
        with self.assertRaises(UserError), self.cr.savepoint():
            stale.action_apply()
        self.assertEqual(group.credit_ids.amount_total, 50)

    def test_failed_reposting_rolls_back_credit_payment_and_reconciliation(self):
        invoice = self._invoice(registered_payment=True)
        payment = invoice.reconciled_payment_ids
        group = self._group(invoice)
        group.action_create_credit()
        with patch.object(type(group.credit_ids), 'action_post', side_effect=UserError('Posting blocked')):
            with self.assertRaises(UserError):
                self._adjustment(group, 50).action_apply()
        self.assertEqual(payment.amount, 40)
        self.assertEqual(group.credit_ids.amount_total, 60)
        self.assertEqual(group.credit_ids.state, 'posted')
        self.assertEqual(invoice.amount_residual, 0)
        self.assertEqual(group.adjustment_revision, 0)

    def test_invalid_amount_and_zero_payment_are_rejected(self):
        invoice = self._invoice(registered_payment=True)
        group = self._group(invoice)
        group.action_create_credit()
        for amount in (0, -1, 101, 100):
            with self.subTest(amount=amount), self.assertRaises(UserError), self.cr.savepoint():
                self._adjustment(group, amount).action_apply()
        self.assertEqual(group.credit_ids.amount_total, 60)

    def test_financial_references_and_generated_lines_cannot_be_written_directly(self):
        group = self._group(self._invoice())
        group.action_create_credit()
        for values in ({'state': 'draft'}, {'credit_ids': [Command.clear()]}, {'adjustment_revision': 0}):
            with self.assertRaises(UserError), self.cr.savepoint():
                group.write(values)
        with self.assertRaises(UserError), self.cr.savepoint():
            group.line_ids.amount = 40

    def test_missing_actual_payment_does_not_partially_generate_notes(self):
        group = self._group(self._invoice(), amount=20)
        with self.assertRaises(UserError):
            group.action_create_credit()
        self.assertFalse(group.credit_ids)
        self.assertEqual(group.state, 'draft')
        self.assertEqual(group.line_ids.invoice_id.amount_residual, 60)

    def test_shared_payment_is_not_silently_reallocated(self):
        invoice = self._invoice(registered_payment=True)
        other = self._invoice(payment_amount=None)
        payment = invoice.reconciled_payment_ids
        payment.action_draft()
        payment.amount = 80
        payment.action_post()
        debit = payment.move_id.line_ids.filtered(lambda line: line.account_type == 'liability_payable')
        for bill in (invoice, other):
            credit = bill.line_ids.filtered(lambda line: line.account_type == 'liability_payable')
            self.env['account.partial.reconcile'].create({
                'debit_move_id': debit.id, 'credit_move_id': credit.id,
                'amount': 40, 'debit_amount_currency': 40, 'credit_amount_currency': 40,
            })
        group = self._group(invoice, amount=50)
        with self.assertRaises(UserError):
            group.action_create_credit()
        self.assertEqual(payment.amount, 80)
        self.assertEqual(invoice.amount_residual, 60)
        self.assertEqual(other.amount_residual, 60)

    def test_adjustment_preserves_taxes_and_balanced_entries(self):
        tax = self.env['account.tax'].create({
            'name': 'Vendor adjustment VAT 16', 'amount': 16, 'type_tax_use': 'purchase',
            'company_id': self.company.id,
        })
        invoice = self._invoice(registered_payment=True, tax=tax)
        payment = invoice.reconciled_payment_ids
        group = self._group(invoice, amount=76)
        group.action_create_credit()
        self._adjustment(group, 58).action_apply()
        self.assertEqual(group.credit_ids.amount_total, 58)
        self.assertEqual(group.credit_ids.amount_tax, 8)
        self.assertEqual(group.credit_ids.invoice_line_ids.tax_ids, tax)
        self.assertEqual(payment.amount, 58)
        self.assertEqual(invoice.amount_total, 116)
        self.assertEqual(invoice.amount_residual, 0)
        for move in group.credit_ids | payment.move_id | invoice:
            self.assertEqual(sum(move.line_ids.mapped('balance')), 0)

    def test_adjustment_in_same_foreign_currency_settles_invoice(self):
        currency = self.env.ref('base.EUR') if self.company.currency_id != self.env.ref('base.EUR') else self.env.ref('base.USD')
        currency.active = True
        self.env['res.currency.rate'].create({
            'currency_id': currency.id, 'company_id': self.company.id,
            'name': fields.Date.today(), 'rate': 2,
        })
        invoice = self._invoice(registered_payment=True, currency=currency)
        payment = invoice.reconciled_payment_ids
        group = self._group(invoice)
        group.action_create_credit()
        self._adjustment(group, 50).action_apply()
        self.assertEqual(group.credit_ids.currency_id, currency)
        self.assertEqual(payment.currency_id, currency)
        self.assertEqual(payment.amount, 50)
        self.assertEqual(group.credit_ids.amount_total, 50)
        self.assertEqual(invoice.amount_residual, 0)

    def test_requested_1000_invoice_150_credit_and_850_payment(self):
        invoice = self._invoice(registered_payment=True, payment_amount=800, total=1000)
        payment = invoice.reconciled_payment_ids
        group = self._group(invoice, amount=200)
        group.action_create_credit()
        self._adjustment(group, 150).action_apply()
        self.assertEqual(group.credit_ids.amount_total, 150)
        self.assertEqual(payment.amount, 850)
        self.assertEqual(invoice.amount_residual, 0)

    def test_external_credit_edit_is_not_silently_overwritten(self):
        invoice = self._invoice(registered_payment=True)
        group = self._group(invoice)
        group.action_create_credit()
        credit = group.credit_ids
        credit.line_ids.remove_move_reconcile()
        credit.button_draft()
        credit.invoice_line_ids.price_unit = 50
        credit.action_post()
        group._reconcile_credit_with_invoices(credit, group.line_ids)
        with self.assertRaises(UserError), self.cr.savepoint():
            self._adjustment(group, 55).action_apply()
        self.assertEqual(credit.amount_total, 50)

    def test_legacy_credit_link_is_recovered_and_preview_is_correct(self):
        invoice = self._invoice(registered_payment=True)
        group = self._group(invoice)
        group.action_create_credit()
        self.env.cr.execute('UPDATE vendor_credit_group_line SET credit_id=NULL WHERE id=%s', [group.line_ids.id])
        group.line_ids.invalidate_recordset(['credit_id', 'proposed_payment_amount'])
        self.assertEqual(group.line_ids.proposed_payment_amount, 40)
        self.env['vendor.credit.group']._migrate_reason_catalog()
        self.assertEqual(group.line_ids.credit_id, group.credit_ids)

    def _material_group(self, quantity=6):
        product = self.env['product.product'].create({
            'name': 'Material devolución QA', 'default_code': 'VCRMAT',
            'type': 'consu', 'is_storable': True,
            'property_account_expense_id': self.expense.id,
        })
        invoice = self._invoice(registered_payment=True, product=product)
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.company.id)], limit=1)
        picking = self.env['stock.picking'].create({
            'picking_type_id': warehouse.in_type_id.id, 'partner_id': self.partner.id,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': warehouse.lot_stock_id.id,
            'move_ids': [Command.create({'product_id': product.id,
                'product_uom': product.uom_id.id, 'product_uom_qty': 10,
                'location_id': self.env.ref('stock.stock_location_suppliers').id,
                'location_dest_id': warehouse.lot_stock_id.id})],
        })
        picking.action_confirm()
        picking.move_ids.quantity = 10
        picking.move_ids.picked = True
        picking.with_context(skip_sms=True, skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')
        group = self._group(invoice)
        reason = self.env['vendor.credit.reason'].create({'name': 'Devolución QA', 'operation_type': 'return'})
        group.reason_id = reason
        group.material_line_ids = [Command.create({
            'allocation_id': group.line_ids.id, 'invoice_line_id': invoice.invoice_line_ids.id,
            'quantity': quantity, 'receipt_move_id': picking.move_ids.id,
        })]
        return group, picking, product

    def test_monetary_credit_has_no_products_and_uses_configured_accounts(self):
        invoice = self._invoice(registered_payment=True)
        account = self.expense.copy({'code': 'VCRBON', 'name': 'Bonificaciones proveedor'})
        reason = self.env['vendor.credit.reason'].create({
            'name': 'Solo dinero QA', 'account_id': account.id, 'payable_account_id': self.payable.id})
        group = self._group(invoice)
        group.reason_id = reason
        group.action_create_credit()
        self.assertFalse(group.credit_ids.invoice_line_ids.product_id)
        self.assertEqual(group.credit_ids.invoice_line_ids.account_id, account)
        self.assertEqual(invoice.amount_residual, 0)
        self.assertFalse(group.return_picking_ids)

    def test_adjustment_uses_new_reason_account(self):
        invoice = self._invoice(registered_payment=True)
        group = self._group(invoice)
        group.action_create_credit()
        account = self.expense.copy({'code': 'VCRADJ'})
        reason = self.env['vendor.credit.reason'].create({'name': 'Cuenta ajuste QA', 'account_id': account.id})
        wizard = self._adjustment(group, 50)
        wizard.reason_id = reason
        wizard.action_apply()
        self.assertEqual(group.credit_ids.invoice_line_ids.account_id, account)
        self.assertEqual(invoice.reconciled_payment_ids.amount, 50)
        self.assertEqual(invoice.amount_residual, 0)

    def test_wrong_payable_account_rolls_back_payment(self):
        invoice = self._invoice(registered_payment=True)
        group = self._group(invoice)
        account = self.payable.copy({'code': 'VCROTHER'})
        group.reason_id = self.env['vendor.credit.reason'].create({'name': 'Otro pasivo QA', 'payable_account_id': account.id})
        with self.assertRaises(UserError):
            group.action_create_credit()
        self.assertEqual(invoice.reconciled_payment_ids.amount, 40)
        self.assertFalse(group.credit_ids)

    def test_return_captures_material_creates_single_native_picking_and_reserves(self):
        group, receipt, product = self._material_group()
        group.action_create_credit()
        returned = group.return_picking_ids
        self.assertEqual(len(returned), 1)
        self.assertEqual(returned.move_ids.origin_returned_move_id, receipt.move_ids)
        self.assertEqual(returned.move_ids.product_uom_qty, 6)
        self.assertNotEqual(returned.state, 'done')
        self.assertEqual(group.credit_ids.invoice_line_ids.product_id, product)
        self.assertEqual(group.credit_ids.invoice_line_ids.quantity, 6)
        self.assertEqual(group.credit_ids.amount_total, 60)
        self.assertEqual(group.line_ids.invoice_id.amount_residual, 0)
        group.action_create_credit()
        self.assertEqual(group.return_picking_ids, returned)
        self._adjustment(group, 50).action_apply()
        self.assertEqual(group.return_picking_ids, returned)
        self.assertEqual(returned.move_ids.product_uom_qty, 6)
        returned.move_ids.quantity = 6
        returned.move_ids.picked = True
        returned.with_context(skip_sms=True, skip_backorder=True).button_validate()
        self.assertEqual(returned.state, 'done')
        self.assertEqual(self.env['stock.quant']._get_available_quantity(product, receipt.location_dest_id), 4)
        with self.assertRaises(UserError):
            group.action_cancel()

    def test_pending_return_cancelled_and_material_is_immutable(self):
        group, receipt, product = self._material_group()
        group.action_create_credit()
        with self.assertRaises(UserError), self.cr.savepoint():
            group.material_line_ids.quantity = 5
        returned = group.return_picking_ids
        group.action_cancel()
        self.assertEqual(returned.state, 'cancel')
        self.assertEqual(group.state, 'cancel')
        group.action_draft()
        group.action_create_credit()
        self.assertNotEqual(group.return_picking_ids, returned)

    def test_pending_return_already_uses_receipt_quantity(self):
        group, receipt, product = self._material_group()
        wizard = self.env['stock.return.picking'].create({'picking_id': receipt.id})
        wizard.product_return_moves.quantity = 5
        wizard._create_return()
        with self.assertRaises(UserError):
            group.action_create_credit()
        self.assertFalse(group.credit_ids)
        self.assertEqual(group.line_ids.invoice_id.amount_residual, 60)

    def test_material_required_and_operation_cannot_change_after_posting(self):
        group, receipt, product = self._material_group()
        material = group.material_line_ids
        values = {'allocation_id': group.line_ids.id, 'invoice_line_id': material.invoice_line_id.id,
                  'quantity': 6, 'receipt_move_id': receipt.move_ids.id}
        material.unlink()
        with self.assertRaises(UserError):
            group.action_create_credit()
        group.material_line_ids = [Command.create(values)]
        group.action_create_credit()
        wizard = self._adjustment(group, 50)
        wizard.reason_id = self.env.ref('entintados_pdv.vendor_credit_reason_bonus')
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_monetary_reason_removes_products_without_moving_received_stock(self):
        group, receipt, product = self._material_group()
        group.material_line_ids.unlink()
        group.reason_id = self.env.ref('entintados_pdv.vendor_credit_reason_bonus')
        group.action_create_credit()
        self.assertFalse(group.credit_ids.invoice_line_ids.product_id)
        self.assertFalse(group.return_picking_ids)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(product, receipt.location_dest_id), 10)

    def test_return_tax_inclusive_amount_and_payment_adjustment(self):
        group, receipt, product = self._material_group()
        # Add tax to the original source before recreating the draft grouping.
        invoice = group.line_ids.invoice_id
        invoice.line_ids.remove_move_reconcile()
        invoice.button_draft()
        tax = self.env['account.tax'].create({'name': 'Material VAT 16 QA', 'amount': 16,
                                             'type_tax_use': 'purchase'})
        invoice.invoice_line_ids.tax_ids = tax
        invoice.action_post()
        payment = self.env['account.payment'].search([('partner_id', '=', self.partner.id)], order='id desc', limit=1)
        (invoice | payment.move_id).line_ids.filtered(lambda line: line.account_id == self.payable).reconcile()
        group.line_ids.amount = 69.6
        group.action_create_credit()
        self.assertAlmostEqual(group.credit_ids.amount_total, 69.6)
        self.assertAlmostEqual(group.credit_ids.amount_tax, 9.6)
        self.assertEqual(group.credit_ids.invoice_line_ids.quantity, 6)
        self.assertEqual(invoice.amount_residual, 0)
        self.assertAlmostEqual(payment.amount, 46.4)
