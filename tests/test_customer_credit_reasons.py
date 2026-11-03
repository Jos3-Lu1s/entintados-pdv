from odoo import Command
from odoo.exceptions import AccessError, ValidationError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestCustomerCreditReasons(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Cliente motivos QA', 'phone': '5551234567', 'is_customer': True})
        cls.reason = cls.env.ref('entintados_pdv.customer_credit_reason_bonus')
        cls.journal = cls.env['account.journal'].create({'name': 'Motivos cliente QA', 'code': 'MCQA', 'type': 'sale'})

    def test_catalog_permissions_and_company_scope(self):
        user = new_test_user(self.env, login='customer_reason_reader', groups='base.group_user,account.group_account_invoice')
        self.reason.with_user(user).check_access('read')
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['customer.credit.reason'].with_user(user).create({'name': 'No permitido'})
        manager = new_test_user(self.env, login='customer_reason_manager', groups='base.group_user,account.group_account_manager')
        self.env['customer.credit.reason'].with_user(manager).create({'name': 'Motivo QA'})
        other = self.env['res.company'].create({'name': 'Motivos otra compañía QA'})
        private = self.env['customer.credit.reason'].create({'name': 'Privado QA', 'company_id': other.id})
        self.assertFalse(self.env['customer.credit.reason'].with_user(user).search([('id', '=', private.id)]))

    def test_adjustment_selection_supplies_description_and_preserves_details(self):
        adjustment = self.env['customer.account.adjustment'].create({'partner_id': self.partner.id, 'reason_id': self.reason.id})
        self.assertEqual(adjustment.reason, self.reason.name)
        adjustment.reason = 'Detalle de bonificación'
        self.assertEqual(adjustment.reason, 'Detalle de bonificación')
        adjustment.reason_id = self.env.ref('entintados_pdv.customer_credit_reason_price')
        self.assertEqual(adjustment.reason, adjustment.reason_id.name)

    def test_archived_and_foreign_reasons_rejected(self):
        archived = self.reason.copy({'active': False})
        other = self.env['res.company'].create({'name': 'Motivo compañía ajena QA'})
        foreign = self.reason.copy({'company_id': other.id})
        for reason in archived | foreign:
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.env['customer.account.adjustment'].create({'partner_id': self.partner.id, 'reason_id': reason.id})

    def test_legacy_reason_text_remains_supported(self):
        adjustment = self.env['customer.account.adjustment'].create({'partner_id': self.partner.id, 'reason': 'Motivo anterior'})
        self.assertEqual(adjustment.reason, 'Motivo anterior')
        self.assertFalse(adjustment.reason_id)

    def test_reversal_carries_reason_only_for_customer_credit(self):
        invoice = self.env['account.move'].create({'move_type': 'out_invoice', 'partner_id': self.partner.id, 'journal_id': self.journal.id})
        wizard = self.env['account.move.reversal'].create({'move_ids': [Command.set(invoice.ids)],
            'company_id': self.env.company.id, 'journal_id': self.journal.id, 'customer_credit_reason_id': self.reason.id})
        values = wizard._prepare_default_reversal(invoice)
        self.assertEqual(values['customer_credit_reason_id'], self.reason.id)
        self.assertIn(self.reason.name, values['ref'])
        self.reason.active = False
        with self.assertRaises(ValidationError):
            wizard._prepare_default_reversal(invoice)
