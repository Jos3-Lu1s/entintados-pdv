# -*- coding: utf-8 -*-

from odoo import Command
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPosGlobalPromotionParity(TransactionCase):
    """Pruebas de paridad de promociones globales de orden en Punto de Venta y sincronizacion con Ventas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Test Promociones Globales PDV',
            'phone': '5551234567',
            'is_customer': True,
            'company_id': cls.company.id,
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Promociones Globales',
            'currency_id': cls.company.currency_id.id,
            'company_id': cls.company.id,
        })
        cls.pos_config = cls.env['pos.config'].create({
            'name': 'Caja Promociones Globales',
            'pricelist_id': cls.pricelist.id,
            'company_id': cls.company.id,
        })
        cls.session = cls.env['pos.session'].create({
            'config_id': cls.pos_config.id,
            'user_id': cls.env.uid,
            'state': 'opened',
        })
        cls.product_a = cls.env['product.product'].create({
            'name': 'Esmalte Sintetico Blanco',
            'list_price': 500.0,
            'type': 'consu',
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
            'company_id': cls.company.id,
        })
        cls.product_b = cls.env['product.product'].create({
            'name': 'Sellador Vinilico Concentrado',
            'list_price': 1000.0,
            'type': 'consu',
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
            'company_id': cls.company.id,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Campana Global 20 Porciento',
            'program_type': 'promotion',
            'applies_on': 'current',
            'company_id': cls.company.id,
        })
        cls.reward = cls.env['loyalty.reward'].create({
            'program_id': cls.program.id,
            'reward_type': 'discount',
            'discount': 20.0,
            'discount_mode': 'percent',
            'discount_applicability': 'order',
        })

    def test_global_promo_applies_to_all_eligible_lines_in_quotation(self):
        """Verifica que la promocion global de orden se refleje en todas las lineas elegibles al cotizar."""
        ticket = self.env['pos.order'].create({
            'session_id': self.session.id,
            'company_id': self.company.id,
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'amount_total': 1200.0,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'state': 'draft',
            'lines': [
                Command.create({
                    'product_id': self.product_a.id,
                    'qty': 1,
                    'price_unit': 500.0,
                    'discount': 20.0,
                    'price_subtotal': 400.0,
                    'price_subtotal_incl': 400.0,
                    'pricing_rule_type': 'promo',
                    'price_origin': 'promo',
                    'price_origin_label': 'Promocion global',
                    'promo_reward_id': self.reward.id,
                    'tax_ids': [Command.clear()],
                }),
                Command.create({
                    'product_id': self.product_b.id,
                    'qty': 1,
                    'price_unit': 1000.0,
                    'discount': 20.0,
                    'price_subtotal': 800.0,
                    'price_subtotal_incl': 800.0,
                    'pricing_rule_type': 'promo',
                    'price_origin': 'promo',
                    'price_origin_label': 'Promocion global',
                    'promo_reward_id': self.reward.id,
                    'tax_ids': [Command.clear()],
                }),
            ],
        })

        result = ticket.action_create_sale_quotation()
        quote = self.env['sale.order'].browse(result['id'])
        self.assertEqual(quote.state, 'draft')
        self.assertEqual(quote.amount_total, 1200.0)
        self.assertEqual(quote.order_promo_reward_id, self.reward)
        self.assertEqual(len(quote.order_line), 2)

        for line in quote.order_line:
            self.assertEqual(line.discount, 20.0)
            self.assertEqual(line.pricing_rule_type, 'promo')
            self.assertEqual(line.price_origin, 'promo')
            self.assertEqual(line.promo_reward_id, self.reward)

    def test_global_promo_removal_preserves_restored_commercial_agreement(self):
        """Verifica que al remover la promocion global de la orden, cada partida vuelve a su acuerdo comercial."""
        # Se pacta un acuerdo comercial de 10% en product_a para el cliente
        self.env['res.partner.discount.rule'].create({
            'partner_id': self.partner.id,
            'product_id': self.product_a.id,
            'discount': 10.0,
            'company_id': self.company.id,
        })

        # Al removerse la promocion global en el PDV, product_a regresa a su 10% y product_b a 0%
        ticket = self.env['pos.order'].create({
            'session_id': self.session.id,
            'company_id': self.company.id,
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'amount_total': 1450.0,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'state': 'draft',
            'lines': [
                Command.create({
                    'product_id': self.product_a.id,
                    'qty': 1,
                    'price_unit': 500.0,
                    'discount': 10.0,
                    'price_subtotal': 450.0,
                    'price_subtotal_incl': 450.0,
                    'pricing_rule_type': 'product',
                    'price_origin': 'agreement',
                    'price_origin_label': 'Acuerdo comercial 10%',
                    'promo_reward_id': False,
                    'tax_ids': [Command.clear()],
                }),
                Command.create({
                    'product_id': self.product_b.id,
                    'qty': 1,
                    'price_unit': 1000.0,
                    'discount': 0.0,
                    'price_subtotal': 1000.0,
                    'price_subtotal_incl': 1000.0,
                    'pricing_rule_type': 'none',
                    'price_origin': 'pricelist',
                    'price_origin_label': '',
                    'promo_reward_id': False,
                    'tax_ids': [Command.clear()],
                }),
            ],
        })

        result = ticket.action_create_sale_quotation()
        quote = self.env['sale.order'].browse(result['id'])
        self.assertEqual(quote.amount_total, 1450.0)
        self.assertFalse(quote.order_promo_reward_id)

        line_a = quote.order_line.filtered(lambda l: l.product_id == self.product_a)
        line_b = quote.order_line.filtered(lambda l: l.product_id == self.product_b)

        self.assertEqual(line_a.discount, 10.0)
        self.assertEqual(line_a.pricing_rule_type, 'product')
        self.assertFalse(line_a.promo_reward_id)

        self.assertEqual(line_b.discount, 0.0)
        self.assertFalse(line_b.promo_reward_id)

    def test_global_promo_excludes_colorant_and_fixed_price(self):
        """Verifica que colorantes formulados y lineas con precio fijo no sean asignadas como lineas de promocion."""
        point = self.env.ref('entintados_pdv.uom_tint_point')
        base = self.env['product.product'].create({
            'name': 'Base Entintada Promocion QA',
            'list_price': 500.0,
            'type': 'consu',
            'is_storable': True,
            'tint_role': 'base',
            'tint_base_type_id': self.env['tint.base.type'].search([], limit=1).id,
            'tint_size_id': self.env['tint.size'].search([], limit=1).id,
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
            'company_id': self.company.id,
        })
        colorant = self.env['product.product'].create({
            'name': 'Colorante Negro de Formula',
            'list_price': 100.0,
            'type': 'consu',
            'is_storable': True,
            'uom_id': point.id,
            'tint_role': 'colorant',
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
            'company_id': self.company.id,
        })
        product_fixed = self.env['product.product'].create({
            'name': 'Brocha Precio Fijo Inviolable',
            'list_price': 250.0,
            'type': 'consu',
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
            'company_id': self.company.id,
        })

        ticket = self.env['pos.order'].create({
            'session_id': self.session.id,
            'company_id': self.company.id,
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'amount_total': 650.0,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'state': 'draft',
            'lines': [
                Command.create({
                    'product_id': base.id,
                    'qty': 1,
                    'price_unit': 500.0,
                    'discount': 20.0,
                    'price_subtotal': 400.0,
                    'price_subtotal_incl': 400.0,
                    'pricing_rule_type': 'promo',
                    'price_origin': 'promo',
                    'promo_reward_id': self.reward.id,
                    'tint_color_code': 'QA-TEST',
                    'tax_ids': [Command.clear()],
                }),
                Command.create({
                    'product_id': product_fixed.id,
                    'qty': 1,
                    'price_unit': 250.0,
                    'discount': 0.0,
                    'price_subtotal': 250.0,
                    'price_subtotal_incl': 250.0,
                    'pricing_rule_type': 'fixed_price',
                    'price_origin': 'agreement',
                    'price_origin_label': 'Precio Fijo',
                    'promo_reward_id': False,
                    'tax_ids': [Command.clear()],
                }),
            ],
        })

        # Adjuntar colorante a la base
        base_line = ticket.lines.filtered(lambda l: l.product_id == base)
        self.env['pos.order.line'].create({
            'order_id': ticket.id,
            'product_id': colorant.id,
            'qty': 10,
            'price_unit': 0.0,
            'discount': 0.0,
            'price_subtotal': 0.0,
            'price_subtotal_incl': 0.0,
            'combo_parent_id': base_line.id,
            'pricing_rule_type': 'none',
            'price_origin': 'tint',
            'promo_reward_id': False,
            'tax_ids': [Command.clear()],
        })

        result = ticket.action_create_sale_quotation()
        quote = self.env['sale.order'].browse(result['id'])
        line_fixed = quote.order_line.filtered(lambda l: l.product_id == product_fixed)

        self.assertFalse(line_fixed.promo_reward_id)
        self.assertEqual(line_fixed.discount, 0.0)
        self.assertEqual(line_fixed.pricing_rule_type, 'fixed_price')
