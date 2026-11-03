from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools.convert import convert_file


@tagged('post_install', '-at_install')
class TestVendorCreditReasons(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.accountant = new_test_user(cls.env, login='credit_reason_accountant',
                                       groups='base.group_user,account.group_account_invoice')
        cls.manager = new_test_user(cls.env, login='credit_reason_manager',
                                    groups='base.group_user,account.group_account_manager')

    def test_accountant_reads_catalog_and_manager_maintains_it(self):
        reason = self.env['vendor.credit.reason'].with_user(self.manager).create({'name': 'Bonificación QA'})
        reason.with_user(self.accountant).check_access('read')
        for operation in (lambda: self.env['vendor.credit.reason'].with_user(self.accountant).create({'name': 'Not allowed'}),
                          lambda: reason.with_user(self.accountant).write({'name': 'Not allowed'}),
                          lambda: reason.with_user(self.accountant).unlink()):
            with self.assertRaises(AccessError), self.cr.savepoint():
                operation()
        reason.with_user(self.manager).active = False
        self.assertFalse(reason.active)

    def test_reason_company_scope(self):
        other = self.env['res.company'].create({'name': 'Other credit reasons'})
        restricted = self.env['vendor.credit.reason'].create({'name': 'Private reason', 'company_id': other.id})
        shared = self.env['vendor.credit.reason'].create({'name': 'Shared reason', 'company_id': False})
        reasons = self.env['vendor.credit.reason'].with_user(self.accountant)
        self.assertFalse(reasons.search([('id', '=', restricted.id)]))
        self.assertEqual(reasons.search([('id', '=', shared.id)]), shared)

    def test_accounts_require_company_and_correct_type(self):
        account = self.env['account.account'].create({
            'name': 'Reason expense QA', 'code': 'RSACCT', 'account_type': 'expense',
            'company_ids': [Command.set(self.env.company.ids)]})
        for values in (
                {'company_id': False, 'account_id': account.id},
                {'payable_account_id': account.id},
                {'company_id': self.env['res.company'].create({'name': 'Other reason accounts'}).id,
                 'account_id': account.id}):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.env['vendor.credit.reason'].create(dict(values, name='Invalid account QA'))
        reason = self.env['vendor.credit.reason'].create({'name': 'Valid account QA', 'account_id': account.id})
        self.assertEqual(reason.account_id, account)

    def test_migration_keeps_legacy_reason_text_and_reuses_catalog(self):
        supplier = self.env['res.partner'].create({'name': 'Reason migration supplier', 'is_supplier': True})
        journal = self.env['account.journal'].create({'name': 'Reason migration purchases', 'code': 'RSMG', 'type': 'purchase'})
        groups = self.env['vendor.credit.group'].create([
            {'partner_id': supplier.id, 'journal_id': journal.id, 'reason': 'Legacy supplier bonus'}
            for _ in range(2)
        ])
        self.env['vendor.credit.group']._migrate_reason_catalog()
        self.env['vendor.credit.group']._migrate_reason_catalog()
        self.assertEqual(len(groups.reason_id), 1)
        self.assertEqual(groups.reason_id.name, 'Legacy supplier bonus')
        self.assertEqual(groups.mapped('reason'), ['Legacy supplier bonus', 'Legacy supplier bonus'])

    def test_catalog_selection_supplies_reason_description(self):
        supplier = self.env['res.partner'].create({'name': 'Reason selected supplier', 'is_supplier': True})
        reason = self.env.ref('entintados_pdv.vendor_credit_reason_bonus')
        group = self.env['vendor.credit.group'].create({'partner_id': supplier.id, 'reason_id': reason.id})
        self.assertEqual(group.reason, reason.name)
        reason = reason.copy({'name': 'Archived QA reason'})
        reason.active = False
        group.reason_id = reason
        with self.assertRaises(UserError), self.cr.savepoint():
            group.action_create_credit()

    def test_loading_sequence_data_does_not_reset_its_counter(self):
        sequence = self.env.ref('entintados_pdv.seq_vendor_credit_group')
        sequence.number_next_actual = 123
        convert_file(self.env, 'entintados_pdv', 'data/note_seq.xml', {}, mode='update')
        sequence.invalidate_recordset()
        self.assertEqual(sequence.number_next_actual, 123)

    def test_migration_moves_an_old_reset_sequence_after_existing_folios(self):
        supplier = self.env['res.partner'].create({'name': 'Sequence migration supplier', 'is_supplier': True})
        self.env['vendor.credit.group'].create({
            'name': 'NCD-00999', 'partner_id': supplier.id,
            'reason_id': self.env.ref('entintados_pdv.vendor_credit_reason_bonus').id,
        })
        sequence = self.env.ref('entintados_pdv.seq_vendor_credit_group')
        sequence.number_next_actual = 1
        self.env['vendor.credit.group']._migrate_reason_catalog()
        self.assertEqual(sequence.number_next_actual, 1000)
        self.env['vendor.credit.group']._migrate_reason_catalog()
        self.assertEqual(sequence.number_next_actual, 1000)
