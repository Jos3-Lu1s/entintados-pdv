"""Quotation conversion must leave no cancelled sale or sequence use in POS."""
import logging
import os
import sys
import unittest
from unittest.mock import patch
import odoo
if not hasattr(odoo, 'Command'):
    import odoo.init
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.modules.registry import Registry
from odoo.tools import config

database = sys.argv[1]
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http',
                     '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
                     '--db_password=' + os.environ['PASSWORD']])
Registry(database)
logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
from odoo.tests import common
from odoo.tests.common import new_test_user
from odoo.addons.entintados_pdv.tests.test_pos_sale_quotation import TestPosSaleQuotation
common.get_db_name = lambda: database


class WithoutPosOrder(TestPosSaleQuotation):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.registry.init_models(cls.env.cr, ['sale.order'], {'module': 'entintados_pdv'}, install=False)

    def payload(self, ticket):
        data = {name: ticket[name] for name in (
            'uuid', 'state', 'amount_total', 'amount_tax', 'amount_paid', 'amount_return', 'name')}
        data.update(session_id=ticket.session_id.id, partner_id=ticket.partner_id.id,
                    pricelist_id=ticket.pricelist_id.id, fiscal_position_id=ticket.fiscal_position_id.id)
        data['lines'] = [Command.create({
            'uuid': line.uuid, 'product_id': line.product_id.id, 'qty': line.qty,
            'price_unit': line.price_unit, 'discount': line.discount,
            'price_subtotal': line.price_subtotal, 'price_subtotal_incl': line.price_subtotal_incl,
            'tax_ids': [Command.set(line.tax_ids.ids)], 'customer_note': line.customer_note,
            'tint_color_code': line.tint_color_code,
        }) for line in ticket.lines]
        data['relations_uuid_mapping'] = {'pos.order.line': {
            line.uuid: {'combo_parent_id': line.combo_parent_id.uuid}
            for line in ticket.lines if line.combo_parent_id}}
        expected = {'partner_id': ticket.partner_id.id, 'pricelist_id': ticket.pricelist_id.id,
                    'fiscal_position_id': ticket.fiscal_position_id.id,
                    'lines': [[line.uuid, line.product_id.id, line.qty, line.price_unit,
                               line.discount, line.combo_parent_id.uuid or False,
                               line.customer_note or '', line.tint_color_code or False] for line in ticket.lines]}
        return data, expected

    def test_ui_creates_only_quotation_and_retries(self):
        for tinted in (False, True):
            ticket = self._ticket(tinted=tinted, discount=10)
            data, expected = self.payload(ticket)
            ticket.unlink()
            count = self.env['pos.order'].search_count([])
            sequence = self.env['ir.sequence'].search([('code', '=', 'entintados_pdv.branch_order'),
                                                       ('company_id', '=', self.env.company.id)])
            next_number = sequence.number_next_actual
            with patch.object(type(self.env['pos.order']), 'action_pos_order_cancel',
                              side_effect=AssertionError('No cancellation is allowed')):
                result = self.env['pos.order'].create_sale_quotation_from_ui(data, expected)
            quote = self.env['sale.order'].browse(result['id'])
            self.assertEqual(quote.state, 'draft')
            self.assertEqual(quote.amount_total, 403.2 if tinted else 180)
            self.assertEqual(quote.pos_quotation_uuid, data['uuid'])
            self.assertEqual(self.env['pos.order'].search_count([]), count)
            sequence.invalidate_recordset()
            self.assertEqual(sequence.number_next_actual, next_number)
            self.assertEqual(self.env['pos.order'].create_sale_quotation_from_ui(data, expected), result)
            self.assertFalse(quote.picking_ids)
            self.assertFalse(quote.invoice_ids)
            if tinted:
                self.assertEqual(quote.order_line.tint_snapshot['doses'][0]['points'], 12)
                report = self.env['tint.sales.report']
                report.init()
                self.env.flush_all()
                row = report.search([('sale_order_id', '=', quote.id)])
                self.assertEqual(row.status, 'quotation')
                self.assertTrue(row.pos_quotation)
            with self.assertRaises(UserError):
                self.env['pos.order']._process_order(data, False)
            changed = dict(expected, partner_id=False)
            with self.assertRaises(UserError):
                self.env['pos.order'].create_sale_quotation_from_ui(data, changed)

    def test_cashier_permissions_and_taxes(self):
        tax = self.env['account.tax'].create({'name': 'IVA cotización directa', 'amount': 16,
            'amount_type': 'percent', 'type_tax_use': 'sale', 'company_id': self.env.company.id})
        ticket = self._ticket(tinted=True, discount=10, taxes=tax)
        test_env = self.env(context={**self.env.context, 'no_reset_password': True})
        user = new_test_user(test_env, login='quotation_direct_cashier',
            groups='base.group_user,point_of_sale.group_pos_user,sales_team.group_sale_salesman')
        ticket.partner_id = self.env['res.partner'].create({'name': 'Cliente cotización directa',
            'is_customer': True, 'phone': '5551234567', 'user_id': user.id})
        data, expected = self.payload(ticket)
        # Native POS serialization sends unchanged many2many relations as IDs.
        for command in data['lines']:
            command[2]['tax_ids'] = list(ticket.lines.filtered(
                lambda line: line.uuid == command[2]['uuid']).tax_ids.ids)
        ticket.unlink()
        no_sales = new_test_user(test_env, login='quotation_direct_no_sales',
            groups='base.group_user,point_of_sale.group_pos_user')
        with self.assertRaises(AccessError):
            self.env['pos.order'].with_user(no_sales).create_sale_quotation_from_ui(data, expected)
        result = self.env['pos.order'].with_user(user).create_sale_quotation_from_ui(data, expected)
        quote = self.env['sale.order'].browse(result['id'])
        self.assertEqual(quote.user_id, user)
        self.assertAlmostEqual(quote.amount_total, 467.71, places=2)
        self.assertFalse(self.env['pos.order'].search([('uuid', '=', data['uuid'])]))

    def test_synced_draft_is_removed_without_cancellation(self):
        ticket = self._ticket(tinted=True)
        data, expected = self.payload(ticket)
        result = self.env['pos.order'].create_sale_quotation_from_ui(data, expected)
        self.assertFalse(ticket.exists())
        self.assertTrue(self.env['sale.order'].browse(result['id']).exists())

    def test_failed_conversion_preserves_synced_draft(self):
        ticket = self._ticket()
        data, expected = self.payload(ticket)
        data['amount_total'] = 999
        count = self.env['sale.order'].search_count([])
        with self.assertRaises(UserError):
            self.env['pos.order'].create_sale_quotation_from_ui(data, expected)
        self.assertEqual(ticket.state, 'draft')
        self.assertEqual(self.env['sale.order'].search_count([]), count)


names = sys.argv[2:] or ['test_ui_creates_only_quotation_and_retries', 'test_synced_draft_is_removed_without_cancellation',
         'test_failed_conversion_preserves_synced_draft', 'test_cashier_permissions_and_taxes']
result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(WithoutPosOrder(name) for name in names))
raise SystemExit(0 if result.wasSuccessful() else 1)
