from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPosBankPayments(TransactionCase):
    def setUp(self):
        super().setUp()
        self.config = self.env['pos.config'].create({'name': 'Bancos PDV QA'})

    def _prepare(self):
        action = self.config.action_prepare_bank_payments()
        return self.env['pos.payment.method'].search(action['domain'])

    def test_native_methods_are_created_once_and_linked_without_preparation_button(self):
        self.env['pos.payment.method']._ensure_transfer_check_methods()
        methods = self.config.payment_method_ids.filtered(lambda item: item.collection_type in ('transfer', 'check'))
        self.assertEqual(len(methods), 2)
        self.assertEqual(set(methods.mapped('name')), {'Transferencia', 'Cheque'})
        self.assertTrue(all(method.journal_id.type == 'bank' and method.outstanding_account_id for method in methods))
        self.env['pos.payment.method']._ensure_transfer_check_methods()
        self.assertEqual(self.config.payment_method_ids.filtered(lambda item: item.collection_type in ('transfer', 'check')), methods)

    def test_native_methods_are_created_for_parent_and_branch_companies(self):
        branch = self.env['res.company'].create({'name': 'Sucursal QA', 'parent_id': self.env.company.id})
        self.env.user.company_ids |= branch
        branch_config = self.env['pos.config'].with_company(branch).create({
            'name': 'Bancos sucursal QA', 'payment_method_ids': [Command.clear()]})
        self.env['pos.payment.method']._ensure_transfer_check_methods()
        parent_accounts = self.config.payment_method_ids.filtered(
            lambda item: item.collection_type in ('transfer', 'check')).outstanding_account_id
        branch_accounts = branch_config.payment_method_ids.filtered(
            lambda item: item.collection_type in ('transfer', 'check')).outstanding_account_id
        self.assertEqual(len(parent_accounts), 1)
        self.assertEqual(len(branch_accounts), 1)
        self.assertNotEqual(parent_accounts.code, branch_accounts.with_company(branch).code)

    def test_branch_with_card_on_parent_journal_gets_its_own_bank_journal(self):
        parent_journal = self.env['account.journal'].search([
            ('company_id', '=', self.env.company.id), ('type', '=', 'bank')], limit=1)
        branch = self.env['res.company'].create({'name': 'Sucursal QA', 'parent_id': self.env.company.id})
        self.env.user.company_ids |= branch
        card = self.env['pos.payment.method'].with_company(branch).create({
            'name': 'Tarjeta sucursal QA', 'company_id': branch.id, 'journal_id': parent_journal.id})
        branch_config = self.env['pos.config'].with_company(branch).create({
            'name': 'Bancos sucursal QA', 'payment_method_ids': [Command.set(card.ids)]})
        self.env['pos.payment.method']._ensure_transfer_check_methods()
        methods = branch_config.payment_method_ids.filtered(lambda item: item.collection_type in ('transfer', 'check'))
        self.assertEqual(len(methods), 2)
        self.assertEqual(methods.journal_id.company_id, branch)
        self.assertEqual(card.journal_id, parent_journal)

    def test_upgrade_preserves_existing_methods_and_accounts(self):
        methods = self._prepare()
        before = {method.id: (method.name, method.journal_id.id, method.outstanding_account_id.id) for method in methods}
        domain = [('company_id', '=', self.env.company.id), ('collection_type', 'in', ['transfer', 'check'])]
        count = self.env['pos.payment.method'].search_count(domain)
        self.env['pos.payment.method']._ensure_transfer_check_methods()
        self.assertEqual({method.id: (method.name, method.journal_id.id, method.outstanding_account_id.id) for method in methods}, before)
        self.assertEqual(self.env['pos.payment.method'].search_count(domain), count)

    def test_prepares_six_bank_methods_and_reuses_configuration(self):
        methods = self._prepare()
        self.assertEqual(len(methods), 6)
        self.assertEqual(set(methods.mapped('terminal_label')), {'BBVA', 'Santander'})
        self.assertEqual(set(methods.mapped('collection_type')), {'transfer', 'check', 'card'})
        self.assertEqual(set(methods.mapped('type')), {'bank'})
        self.assertEqual(len(methods.outstanding_account_id), 2)
        self.assertFalse(methods - self.config.payment_method_ids)
        for method in methods:
            code = {'transfer': '03', 'check': '02', 'card': '28'}[method.collection_type]
            self.assertEqual(method.l10n_mx_edi_payment_method_id.code, code)
        self.assertEqual(self._prepare(), methods)

    def test_account_can_be_changed_and_is_preserved_on_reprepare(self):
        method = self._prepare().filtered(lambda item: item.collection_type == 'transfer')[:1]
        account = method.outstanding_account_id.copy({'name': 'Cuenta definitiva QA', 'code': 'BKQA01'})
        method.outstanding_account_id = account
        self._prepare()
        self.assertEqual(method.outstanding_account_id, account)

    def test_rejects_wrong_account_or_cash_journal(self):
        method = self._prepare()[:1]
        invalid = self.env['account.account'].create({'name': 'Gasto no valido QA', 'code': 'BKQA02',
            'account_type': 'expense', 'company_ids': [Command.set(self.env.company.ids)]})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            method.outstanding_account_id = invalid
        cash = self.env['account.journal'].create({'name': 'Caja no valida QA', 'code': 'BKQA', 'type': 'cash'})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            method.journal_id = cash

    def test_requires_terminal_name_and_respects_open_session_lock(self):
        method = self._prepare().filtered(lambda item: item.collection_type == 'card')[:1]
        with self.assertRaises(ValidationError), self.cr.savepoint():
            method.terminal_label = False
        self.env['pos.session'].create({'config_id': self.config.id, 'state': 'opened'})
        with self.assertRaises(UserError):
            self.config.action_prepare_bank_payments()
        with self.assertRaises(UserError):
            method.outstanding_account_id = False

    def test_native_bank_payment_posts_to_configured_account(self):
        method = self._prepare().filtered(lambda item: item.collection_type == 'transfer')[:1]
        receivable = self.env['account.account'].create({'name': 'Puente PDV QA', 'code': 'BKQA03',
            'account_type': 'asset_receivable', 'reconcile': True, 'company_ids': [Command.set(self.env.company.ids)]})
        method.receivable_account_id = receivable
        session = self.env['pos.session'].create({'config_id': self.config.id, 'state': 'opened'})
        session._create_combine_account_payment(method, {'amount': 100, 'amount_converted': 100}, 0)
        payment = self.env['account.payment'].search([('pos_session_id', '=', session.id), ('pos_payment_method_id', '=', method.id)])
        self.assertEqual(payment.outstanding_account_id, method.outstanding_account_id)
        self.assertEqual(payment.move_id.state, 'posted')
        pending = payment.move_id.line_ids.filtered(lambda item: item.account_id == method.outstanding_account_id)
        self.assertEqual(sum(pending.mapped('debit')), 100)

    def test_pos_loads_terminal_label_without_accounting_details(self):
        names = self.env['pos.payment.method']._load_pos_data_fields(self.config)
        self.assertIn('collection_type', names)
        self.assertIn('terminal_label', names)
        self.assertNotIn('outstanding_account_id', names)
