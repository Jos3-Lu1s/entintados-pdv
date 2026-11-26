# -*- coding: utf-8 -*-

from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged('post_install', '-at_install')
class TestPosLinePromotionTour(TestPointOfSaleHttpCommon):
    """Flujo de la promoción por línea en caja: aplicar, conservar con la cantidad, quitar
    al acuerdo vigente, volver a aplicar y cobrar. Después, en un pedido nuevo: tarifa por
    esquema, descuento manual que sobrevive a la cantidad y descuento bloqueado en un
    entintado. Por último, combos: precio fijo repartido, acuerdo y promoción del paquete que
    bajan a sus productos y descuento bloqueado en un producto del combo."""

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

        schemes = cls.env['tint.schema'].sudo()
        product_lines = cls.env['lines.product'].sudo()
        scheme = schemes.create({'name': 'Esquema Tour'})
        other_scheme = schemes.create({'name': 'Otro Esquema Tour'})
        scheme_line = product_lines.create({'name': 'Línea Tour', 'scheme': scheme.id})
        other_line = product_lines.create({'name': 'Otra Línea Tour', 'scheme': other_scheme.id})
        cls.scheme_product = cls._create_tour_product('Esmalte Esquema Tour', 'TOUR-ESQ', scheme_line)
        cls._create_tour_product('Esmalte Otro Esquema Tour', 'TOUR-OTRO', other_line)
        cls._create_tour_product('Base Tour Entintado', 'TOUR-BASE')
        cls.env['res.partner.discount.rule'].sudo().create({
            'partner_id': cls.tour_partner.id,
            'applied_on': '0_product',
            'product_id': cls.scheme_product.id,
            'rule_type': 'discount',
            'discount': 10.0,
        })
        scheme_pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Esquema Tour',
            'item_ids': [Command.create({
                'display_applied_on': '3_esquema',
                'esquema_id': scheme.id,
                'compute_price': 'percentage',
                'percent_price': 10.0,
            })],
        })
        cls.main_pos_config.write({
            'use_pricelist': True,
            'pricelist_id': scheme_pricelist.id,
            'available_pricelist_ids': [Command.link(scheme_pricelist.id)],
        })
        cls.tour_partner.property_product_pricelist = scheme_pricelist

        combos = cls.env['product.combo']
        for name, base_price in (('Pintura', 500.0), ('Rodillo', 150.0), ('Brocha', 50.0)):
            product = cls._create_tour_product(f'{name} Combo Tour', f'TOUR-C-{name[:3].upper()}')
            combos |= combos.create({
                'name': f'{name} Tour',
                'base_price': base_price,
                'combo_item_ids': [Command.create({'product_id': product.id})],
            })
        cls.fixed_combo = cls._create_tour_combo('Paquete Fijo Tour', 'TOUR-CFIJO', combos)
        cls.promo_combo = cls._create_tour_combo('Paquete Promo Tour', 'TOUR-CPROMO', combos)
        cls.env['res.partner.discount.rule'].sudo().create([{
            'partner_id': cls.tour_partner.id,
            'applied_on': '0_product',
            'product_id': cls.fixed_combo.id,
            'rule_type': 'fixed_price',
            'fixed_price': 200.0,
        }, {
            'partner_id': cls.tour_partner.id,
            'applied_on': '0_product',
            'product_id': cls.promo_combo.id,
            'rule_type': 'discount',
            'discount': 15.0,
        }])
        cls.combo_program = cls.env['loyalty.program'].sudo().create({
            'name': 'Esp Tour',
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
                'discount_product_ids': [Command.set(cls.promo_combo.ids)],
            })],
        })

    @classmethod
    def _create_tour_product(cls, name, code, product_line=None):
        return cls.env['product.product'].create({
            'name': name,
            'default_code': code,
            'type': 'consu',
            'list_price': 100.0,
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
            'lines_product_id': product_line.id if product_line else False,
        })

    @classmethod
    def _create_tour_combo(cls, name, code, combos):
        return cls.env['product.product'].create({
            'name': name,
            'default_code': code,
            'type': 'combo',
            'list_price': 1000.0,
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
            'combo_ids': [Command.set(combos.ids)],
        })

    def test_scheme_pricelist_fields_loaded(self):
        fields = self.env['product.pricelist.item']._load_pos_data_fields(self.main_pos_config)
        for field_name in ('display_applied_on', 'esquema_id', 'linea_id'):
            self.assertIn(field_name, fields)

    def test_line_promotion_flow(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('EntintadosPosLinePromotionTour')

        line = self.env['pos.order.line'].search([('product_id', '=', self.tour_product.id)], limit=1)
        reward = self.tour_program.reward_ids
        self.assertEqual(line.promo_reward_id, reward)
        self.assertEqual(line.discount, 20.0)
        self.assertEqual(line.pricing_rule_type, 'promo')
        self.assertEqual(line.pricing_rule_origin, 'Promo Tour · 20%')

        fixed_package = self.env['pos.order.line'].search([('product_id', '=', self.fixed_combo.id)], limit=1)
        self.assertEqual(fixed_package.price_unit, 0.0)
        self.assertEqual(fixed_package.pricing_rule_type, 'fixed_price')
        self.assertEqual(fixed_package.combo_line_ids.mapped('price_unit'), [142.86, 42.86, 14.28])
        self.assertEqual(set(fixed_package.combo_line_ids.mapped('price_origin')), {'combo'})

        promo_package = self.env['pos.order.line'].search([('product_id', '=', self.promo_combo.id)], limit=1)
        self.assertEqual(promo_package.promo_reward_id, self.combo_program.reward_ids)
        self.assertEqual(promo_package.discount, 20.0)
        self.assertEqual(set(promo_package.combo_line_ids.mapped('pricing_rule_type')), {'promo'})
        self.assertFalse(promo_package.combo_line_ids.promo_reward_id)
        self.assertEqual(promo_package.order_id.amount_total, 1000.0)
