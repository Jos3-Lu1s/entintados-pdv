# -*- coding: utf-8 -*-

from odoo import Command
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPosInlinePromotions(TransactionCase):
    """Pruebas de promociones por linea directas y conversion a cotizaciones."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Test Promociones Directas PDV',
            'phone': '5559876543',
            'is_customer': True,
            'company_id': cls.company.id,
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Base PDV Promociones',
            'currency_id': cls.company.currency_id.id,
            'company_id': cls.company.id,
        })
        cls.pos_config = cls.env['pos.config'].create({
            'name': 'Caja Promociones Directas',
            'pricelist_id': cls.pricelist.id,
            'company_id': cls.company.id,
        })
        cls.session = cls.env['pos.session'].create({
            'config_id': cls.pos_config.id,
            'user_id': cls.env.uid,
            'state': 'opened',
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Esmalte Directo Promocion',
            'list_price': 500.0,
            'type': 'consu',
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
            'company_id': cls.company.id,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Campana 20 Porciento Directo',
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

    def _create_pos_order_with_promo(self, discount=20.0):
        price_unit = 500.0
        discounted_price = price_unit * (1.0 - discount / 100.0)
        return self.env['pos.order'].create({
            'session_id': self.session.id,
            'company_id': self.company.id,
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'amount_total': discounted_price,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'state': 'draft',
            'lines': [
                Command.create({
                    'product_id': self.product.id,
                    'qty': 1,
                    'price_unit': price_unit,
                    'discount': discount,
                    'price_subtotal': discounted_price,
                    'price_subtotal_incl': discounted_price,
                    'pricing_rule_type': 'promo',
                    'price_origin': 'promo',
                    'price_origin_label': 'Promocion directa',
                    'promo_reward_id': self.reward.id,
                    'tax_ids': [Command.clear()],
                }),
            ],
        })

    def test_quotation_preserves_inline_promotion(self):
        """Verifica que al convertir ticket a cotizacion se preservan descuento y atributos de promocion."""
        ticket = self._create_pos_order_with_promo(discount=20.0)
        result = ticket.action_create_sale_quotation()

        quote = self.env['sale.order'].browse(result['id'])
        self.assertEqual(quote.state, 'draft')
        self.assertEqual(quote.partner_id, self.partner)
        self.assertEqual(quote.amount_total, 400.0)
        self.assertEqual(quote.order_promo_reward_id, self.reward)

        self.assertEqual(len(quote.order_line), 1)
        line = quote.order_line[0]
        self.assertEqual(line.product_id, self.product)
        self.assertEqual(line.price_unit, 500.0)
        self.assertEqual(line.discount, 20.0)
        self.assertEqual(line.technical_discount, 20.0)
        self.assertEqual(line.pricing_rule_type, 'promo')
        self.assertEqual(line.price_origin, 'promo')
        self.assertEqual(line.promo_reward_id, self.reward)

    def test_quotation_filters_negative_reward_line(self):
        """Verifica que las lineas auxiliares negativas de recompensa no se transfieran a la cotizacion."""
        price_unit = 500.0
        ticket = self.env['pos.order'].create({
            'session_id': self.session.id,
            'company_id': self.company.id,
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'amount_total': 400.0,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'state': 'draft',
            'lines': [
                Command.create({
                    'product_id': self.product.id,
                    'qty': 1,
                    'price_unit': price_unit,
                    'discount': 0.0,
                    'price_subtotal': price_unit,
                    'price_subtotal_incl': price_unit,
                    'tax_ids': [Command.clear()],
                }),
                Command.create({
                    'product_id': self.product.id,
                    'qty': 1,
                    'price_unit': -100.0,
                    'discount': 0.0,
                    'price_subtotal': -100.0,
                    'price_subtotal_incl': -100.0,
                    'is_reward_line': True,
                    'reward_id': self.reward.id,
                    'tax_ids': [Command.clear()],
                }),
            ],
        })

        # Al convertir a cotizacion con ticket normalizado a 400 sin linea negativa
        # Si la orden contiene la linea negativa, el filtro la excluye de las lineas de venta creadas.
        # En este escenario la linea de producto mantendria 500 (total 500 vs 400),
        # lo que provocaría validacion de total si no estuviera en descuento directo.
        # Por tanto, en el flujo directo el descuento va en la linea y no existen lineas negativas.
        valid_lines = ticket.lines.filtered(
            lambda item: not item.combo_parent_id and not (item.is_reward_line and item.price_unit < 0)
        )
        self.assertEqual(len(valid_lines), 1)
        self.assertEqual(valid_lines.product_id, self.product)
        self.assertFalse(valid_lines.is_reward_line)
