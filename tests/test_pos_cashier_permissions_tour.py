# -*- coding: utf-8 -*-

from odoo import Command
from odoo.tests import new_test_user, tagged

from odoo.addons.pos_hr.tests.test_frontend import TestPosHrHttpCommon


@tagged('post_install', '-at_install')
class TestPosCashierPermissionsTour(TestPosHrHttpCommon):
    """Permiso de precio y descuento del cajero activo con empleados de `pos_hr`: el cajero sin
    permiso no teclea, el supervisor sí, y lo que teclea el supervisor se conserva al regresar
    el cajero y al elegir un cliente con acuerdo."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({
            'sale_price_unit_editable': False,
            'sale_discount_editable': False,
        })
        cls.main_pos_config.write({
            'restrict_price_control': False,
            'manual_discount': True,
        })

        supervisor_user = new_test_user(
            cls.env,
            login='supervisor_permisos_tour',
            groups='base.group_user,point_of_sale.group_pos_user,'
                   'entintados_pdv.group_sale_edit_price_unit,entintados_pdv.group_sale_edit_discount',
            name='Supervisor Permisos Tour',
        )
        cls.supervisor = cls.env['hr.employee'].create({
            'name': 'Supervisor Permisos Tour',
            'company_id': cls.env.company.id,
            'user_id': supervisor_user.id,
            'pin': '2468',
        })
        cls.cashier = cls.env['hr.employee'].create({
            'name': 'Cajero Sin Permiso Tour',
            'company_id': cls.env.company.id,
            'pin': '1357',
        })
        cls.main_pos_config.write({
            'basic_employee_ids': [Command.link(cls.supervisor.id), Command.link(cls.cashier.id)],
        })

        cls.tour_product = cls.env['product.product'].create({
            'name': 'Esmalte Permisos Tour',
            'default_code': 'TOUR-PERMISOS',
            'type': 'consu',
            'list_price': 100.0,
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
        })
        cls.tour_partner = cls.env['res.partner'].create({
            'name': 'Cliente Permisos Tour',
            'is_customer': True,
            'phone': '5551234568',
        })
        cls.env['res.partner.discount.rule'].sudo().create({
            'partner_id': cls.tour_partner.id,
            'applied_on': '0_product',
            'product_id': cls.tour_product.id,
            'rule_type': 'discount',
            'discount': 10.0,
        })

    def test_cashier_permissions_flow(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('EntintadosPosCashierPermissionsTour')

        line = self.env['pos.order.line'].search([('product_id', '=', self.tour_product.id)], limit=1)
        self.assertEqual(line.price_unit, 80.0)
        self.assertEqual(line.discount, 7.0)
        self.assertEqual(line.pricing_rule_type, 'manual')
        self.assertEqual(line.order_id.partner_id, self.tour_partner)
