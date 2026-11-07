"""Validate grouping labels for native POS lines and the inherited order form."""
import logging
import os
import sys
import unittest
import odoo
if not hasattr(odoo, 'Command'):
    import odoo.init
from odoo import Command
from odoo.modules.registry import Registry
from odoo.tools import config

database = sys.argv[1]
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http',
                     '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
                     '--db_password=' + os.environ['PASSWORD']])
Registry(database)
logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
from odoo.tests import common
from odoo.addons.entintados_pdv.tests.test_pos_sale_quotation import TestPosSaleQuotation
common.get_db_name = lambda: database


class PosTintGroups(TestPosSaleQuotation):
    def test_group_labels_and_native_view(self):
        ticket = self._ticket(tinted=True)
        base = ticket.lines.filtered(lambda line: not line.combo_parent_id)
        component = ticket.lines - base
        before = [(line.id, line.qty, line.price_unit, line.price_subtotal) for line in ticket.lines]
        self.assertEqual(base.tint_line_role, 'base')
        self.assertEqual(component.tint_line_role, 'colorant')
        self.assertEqual(base.tint_group_key, component.tint_group_key)
        self.assertEqual(base.tint_group_name, component.tint_group_name)
        self.assertIn('+ [QA-ROJO] Rojo caja QA', base.tint_group_name)
        other = self._ticket(tinted=True).lines.filtered(lambda line: not line.combo_parent_id)
        self.assertNotEqual(base.tint_group_key, other.tint_group_key)
        regular = self._ticket().lines
        self.assertEqual(regular.tint_line_role, 'regular')
        self.assertFalse(regular.tint_group_name)
        self.assertEqual(before, [(line.id, line.qty, line.price_unit, line.price_subtotal) for line in ticket.lines])
        from odoo.tools.convert import convert_file
        convert_file(self.env, 'entintados_pdv', 'views/pos_order_views.xml', {}, mode='update')
        self.assertIn('pos_tint_grouped_lines', self.env.ref('entintados_pdv.view_pos_order_tint_product_groups').arch_db)
        bundle = self.env['ir.qweb']._get_asset_bundle('web.assets_web', css=False, js=True)
        attachment = bundle.js()
        content = attachment.raw.decode()
        self.assertIn('@entintados_pdv/fields/pos_tint_grouped_lines', content)


result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([PosTintGroups('test_group_labels_and_native_view')]))
raise SystemExit(0 if result.wasSuccessful() else 1)
