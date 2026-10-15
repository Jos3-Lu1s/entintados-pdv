from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
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

    def _invoice(self, registered_payment=False, payment_amount=40):
        invoice = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id,
            'journal_id': self.journal.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'name': 'Purchase', 'account_id': self.expense.id,
                'quantity': 1, 'price_unit': 100, 'tax_ids': [Command.clear()],
            })],
        })
        invoice.action_post()
        if registered_payment:
            outstanding = self.env['account.account'].create({
                'name': 'Credit test outstanding', 'code': 'VCROUT',
                'account_type': 'asset_current', 'reconcile': True,
                'company_ids': [Command.set(self.company.ids)],
            })
            bank = self.env['account.journal'].create({
                'name': 'Credit test bank', 'code': 'VCRB', 'type': 'bank',
                'company_id': self.company.id,
            })
            method = bank.outbound_payment_method_line_ids[:1]
            method.payment_account_id = outstanding
            registered = self.env['account.payment'].create({
                'journal_id': bank.id, 'payment_method_line_id': method.id,
                'partner_id': self.partner.id, 'partner_type': 'supplier',
                'payment_type': 'outbound', 'amount': payment_amount,
                'currency_id': self.company.currency_id.id,
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
            'partner_id': self.partner.id, 'company_id': self.company.id,
            'currency_id': self.company.currency_id.id,
            'journal_id': self.journal.id, 'reason': 'Credit test',
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

    def test_partial_credit_keeps_invoice_balance(self):
        invoice = self._invoice()
        group = self._group(invoice, amount=20)
        group.action_create_credit()
        self.assertEqual(invoice.amount_residual, 40)
        self.assertEqual(invoice.payment_state, 'partial')
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

    def test_legacy_group_opens_existing_credit(self):
        invoice = self._invoice()
        group = self._group(invoice)
        group.action_create_credit()
        credit = group.credit_ids
        group.write({'credit_ids': [Command.clear()], 'credit_id': credit.id})
        self.assertEqual(group.credit_count, 1)
        self.assertEqual(group.action_create_credit()['res_id'], credit.id)

    def test_reset_cancelled_group_edit_and_regenerate(self):
        invoices = self._invoice() | self._invoice()
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
        self.assertEqual(selected_lines[0].invoice_id.amount_residual, 40)
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
