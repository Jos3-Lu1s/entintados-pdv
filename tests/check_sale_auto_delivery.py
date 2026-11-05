"""Exercise automatic sale deliveries with native stock moves; fixtures roll back."""
import logging
import os
import sys
import unittest
import odoo
if not hasattr(odoo, 'Command'):
    import odoo.init
from odoo import Command
from odoo.exceptions import UserError
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
from odoo.addons.entintados_pdv.tests.test_sale_tint import TestSaleTint
common.get_db_name = lambda: database


class AutoDelivery(TestSaleTint):
    def stock(self, order):
        location = order.warehouse_id.lot_stock_id
        self.env['stock.quant']._update_available_quantity(self.base, location, 100)
        self.env['stock.quant']._update_available_quantity(self.colorant, location, 100)
        return location

    def test_auto_tint_delivery(self):
        order = self._order()
        location = self.stock(order)
        order._apply_tint(self.formula, self.base, 2)
        self.assertFalse(order.picking_ids)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.picking_ids.state, 'done')
        self.assertEqual(order.picking_ids.move_ids.product_id, self.base | self.colorant)
        self.assertEqual(order.picking_ids._tint_consumption_moves().quantity, 24)
        self.assertEqual(order.order_line.qty_delivered, 2)
        self.assertEqual(self.base.with_context(location=location.id).qty_available, 98)
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 76)
        order._auto_validate_sale_deliveries()
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 76)

    def test_direct_confirmed_order_and_regular_delivery(self):
        draft = self._order()
        location = self.stock(draft)
        order = self.env['sale.order'].create({'partner_id': self.partner.id, 'state': 'sale',
            'order_line': [Command.create({'product_id': self.base.id, 'product_uom_qty': 3,
                                           'price_unit': 200})]})
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.picking_ids.state, 'done')
        self.assertEqual(order.order_line.qty_delivered, 3)
        self.assertEqual(self.base.with_context(location=location.id).qty_available, 97)

    def test_missing_stock_leaves_draft_and_no_delivery(self):
        order = self._order()
        location = order.warehouse_id.lot_stock_id
        self.env['stock.quant']._update_available_quantity(self.base, location, 100)
        order._apply_tint(self.formula, self.base, 2)
        with self.assertRaises(UserError):
            order.action_confirm()
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.picking_ids)
        self.assertEqual(self.base.with_context(location=location.id).qty_available, 100)

    def test_salesperson_can_confirm_without_inventory_role(self):
        user = new_test_user(self.env(context={**self.env.context, 'no_reset_password': True}),
            login='auto_delivery_sales_only', groups='base.group_user,sales_team.group_sale_salesman')
        partner = self.env['res.partner'].create({'name': 'Cliente entrega automática QA',
            'phone': '5551234567', 'is_customer': True, 'user_id': user.id})
        order = self.env['sale.order'].with_user(user).create({'partner_id': partner.id})
        self.stock(order.sudo())
        self.env['sale.order.line'].with_user(user).create({'order_id': order.id,
            'product_id': self.base.id, 'product_uom_qty': 1, 'price_unit': 200})
        order.action_confirm()
        self.assertEqual(order.sudo().picking_ids.state, 'done')

    def test_missing_lot_rolls_back_confirmation(self):
        order = self._order()
        location = self.stock(order)
        self.base.tracking = 'lot'
        self.env['sale.order.line'].create({'order_id': order.id, 'product_id': self.base.id,
                                          'product_uom_qty': 1, 'price_unit': 200})
        with self.assertRaises(UserError):
            order.action_confirm()
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.picking_ids)
        self.assertEqual(self.base.with_context(location=location.id).qty_available, 100)


names = ['test_auto_tint_delivery', 'test_direct_confirmed_order_and_regular_delivery',
         'test_missing_stock_leaves_draft_and_no_delivery', 'test_missing_lot_rolls_back_confirmation',
         'test_salesperson_can_confirm_without_inventory_role']
result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(AutoDelivery(name) for name in names))
raise SystemExit(0 if result.wasSuccessful() else 1)
