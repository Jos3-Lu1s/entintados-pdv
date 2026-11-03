from odoo import Command, fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBankStatementAudit(TransactionCase):
    def test_bank_statement_creation_keeps_balanced_move_and_audit(self):
        """The native create sequence temporarily has unnamed related records."""
        suspense = self.env['account.account'].create({
            'name': 'Bank audit suspense', 'code': 'BKAUDS',
            'account_type': 'asset_current', 'reconcile': True,
            'company_ids': [Command.set(self.env.company.ids)],
        })
        bank = self.env['account.journal'].create({
            'name': 'Bank audit regression', 'code': 'BKAUD', 'type': 'bank',
            'suspense_account_id': suspense.id,
        })
        statement = self.env['account.bank.statement.line'].create({
            'journal_id': bank.id, 'date': fields.Date.today(),
            'payment_ref': 'Bank audit receipt', 'amount': 2500,
        })
        self.assertEqual(statement.move_id.state, 'posted')
        self.assertAlmostEqual(sum(statement.move_id.line_ids.mapped('balance')), 0)
        self.assertFalse(statement.is_reconciled)
        self.assertTrue(statement.move_id.message_ids)

    def test_relational_audit_value_falls_back_for_missing_display_name(self):
        from unittest.mock import patch
        from odoo.addons.entintados_pdv.models.business_chatter import values

        partner = self.env['res.partner'].create({'name': 'Unnamed relation audit'})
        move = self.env['account.move'].new({'partner_id': partner.id})

        def unnamed(records):
            for record in records:
                record.display_name = False

        with patch.object(type(partner), '_compute_display_name', unnamed):
            partner.invalidate_recordset(['display_name'])
            snapshot = values(move, ['partner_id'])
        self.assertEqual(snapshot['partner_id'][0], (partner.id,))
        self.assertTrue(snapshot['partner_id'][1])
