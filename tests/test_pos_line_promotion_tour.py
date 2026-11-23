# -*- coding: utf-8 -*-

from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged('post_install', '-at_install')
class TestPosLinePromotionTour(TestPointOfSaleHttpCommon):
    """Flujo de la promoción por línea en caja: aplicar, conservar con la cantidad, quitar
    al acuerdo vigente, volver a aplicar y cobrar."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tour_partner = cls.env['res.partner'].create({
            'name': 'Cliente Tour Promo',
            'is_customer': True,
            'phone': '5551234567',
        })
        cls.tour_product = cls.env['product.product'].create({
            'name': 'Esmalte Tour Promo',
            'default_code': 'TOUR-PROMO',
            'type': 'consu',
            'list_price': 100.0,
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
        })
        cls.env['res.partner.discount.rule'].sudo().create({
            'partner_id': cls.tour_partner.id,
            'applied_on': '0_product',
            'product_id': cls.tour_product.id,
            'rule_type': 'discount',
            'discount': 10.0,
        })
        cls.tour_program = cls.env['loyalty.program'].sudo().create({
            'name': 'Promo Tour',
            'program_type': 'promotion',
            'applies_on': 'current',
            'trigger': 'auto',
            'pos_ok': True,
            'rule_ids': [Command.create({'minimum_amount': 0})],
            'reward_ids': [Command.create({
                'reward_type': 'discount',
                'discount': 20.0,
                'discount_mode': 'percent',
                'discount_applicability': 'specific',
                'discount_product_ids': [Command.set(cls.tour_product.ids)],
            })],
        })

    def test_line_promotion_flow(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('EntintadosPosLinePromotionTour')

        line = self.env['pos.order.line'].search([('product_id', '=', self.tour_product.id)], limit=1)
        reward = self.tour_program.reward_ids
        self.assertEqual(line.promo_reward_id, reward)
        self.assertEqual(line.discount, 20.0)
        self.assertEqual(line.pricing_rule_type, 'promo')
        self.assertEqual(line.pricing_rule_origin, 'Promo Tour · 20%')
