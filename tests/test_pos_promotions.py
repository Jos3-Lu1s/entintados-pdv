# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPosPromotions(TransactionCase):
    """Pruebas de modelos y sincronización de promociones entre Punto de Venta y Ventas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Test Promociones PDV',
            'company_id': cls.company.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Pintura Vinilica Promocion',
            'list_price': 500.0,
            'type': 'consu',
            'company_id': cls.company.id,
        })
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Promo 20 Porciento Test',
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
        cls.pos_config = cls.env['pos.config'].create({
            'name': 'TPV Promociones Test',
            'company_id': cls.company.id,
        })

    def test_pos_data_fields_loaded(self):
        """Verifica que los campos de promocion esten expuestos al Punto de Venta."""
        reward_fields = self.env['loyalty.reward']._load_pos_data_fields(self.pos_config)
        self.assertIn('lines_product_id', reward_fields)
        self.assertIn('scheme_id', reward_fields)

        pos_line_fields = self.env['pos.order.line']._load_pos_data_fields(self.pos_config)
        self.assertIn('promo_reward_id', pos_line_fields)

        sale_fields = self.env['sale.order']._load_pos_data_fields(self.pos_config)
        self.assertIn('order_promo_reward_id', sale_fields)

        sale_line_fields = self.env['sale.order.line']._load_pos_data_fields(self.pos_config)
        self.assertIn('promo_reward_id', sale_line_fields)

    def test_loyalty_order_count_includes_pos(self):
        """Verifica que el contador de ordenes del programa considere lineas de PDV."""
        initial_count = self.program.order_count

        session = self.env['pos.session'].create({
            'config_id': self.pos_config.id,
            'user_id': self.env.uid,
        })
        pos_order = self.env['pos.order'].create({
            'session_id': session.id,
            'company_id': self.company.id,
            'partner_id': self.partner.id,
            'lines': [
                (0, 0, {
                    'product_id': self.product.id,
                    'qty': 1,
                    'price_unit': 400.0,
                    'price_subtotal': 400.0,
                    'price_subtotal_incl': 400.0,
                    'promo_reward_id': self.reward.id,
                    'pricing_rule_type': 'promo',
                }),
            ],
            'amount_total': 400.0,
            'amount_tax': 0.0,
            'amount_paid': 400.0,
            'amount_return': 0.0,
            'state': 'paid',
        })
        self.program.invalidate_recordset(['order_count'])
        self.assertEqual(self.program.order_count, initial_count + 1)
