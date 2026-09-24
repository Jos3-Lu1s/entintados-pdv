# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLoyaltyPromotionInline(TransactionCase):
    """Pruebas automatizadas para promociones aplicadas directamente en línea y exclusividad con acuerdos comerciales."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Desactivar programas existentes para aislar la prueba
        cls.env['loyalty.program'].search([]).sudo().write({'active': False})

        cls.partners = cls.env['res.partner']
        cls.rules = cls.env['res.partner.discount.rule']
        cls.templates = cls.env['product.template']
        cls.products = cls.env['product.product']
        cls.schemas = cls.env['tint.schema']
        cls.lines = cls.env['lines.product']
        cls.sale_orders = cls.env['sale.order']

        # Esquema y línea de producto
        cls.schema_a = cls.schemas.create({'name': 'Esquema Pruebas'})
        cls.line_decor = cls.lines.create({
            'name': 'Línea Decorativa',
            'scheme': cls.schema_a.id,
        })

        # Productos
        cls.tmpl_decor = cls.templates.create({
            'name': 'Pintura Vinílica Pro',
            'list_price': 200.0,
            'lines_product_id': cls.line_decor.id,
        })
        cls.prod_decor = cls.tmpl_decor.product_variant_ids[0]

        cls.tmpl_fixed = cls.templates.create({
            'name': 'Sellador Especial',
            'list_price': 100.0,
        })
        cls.prod_fixed = cls.tmpl_fixed.product_variant_ids[0]

        point_uom = cls.env.ref('entintados_pdv.uom_tint_point', raise_if_not_found=False)
        cls.tmpl_colorant = cls.templates.create({
            'name': 'Colorante Negro',
            'list_price': 15.0,
            'tint_role': 'colorant',
            'uom_id': point_uom.id if point_uom else cls.tmpl_decor.uom_id.id,
        })
        cls.prod_colorant = cls.tmpl_colorant.product_variant_ids[0]

        cls.tmpl_gift = cls.templates.create({
            'name': 'Rodillo Profesional',
            'list_price': 80.0,
        })
        cls.prod_gift = cls.tmpl_gift.product_variant_ids[0]

        # Cliente con acuerdos comerciales:
        # - 15% de descuento en Línea Decorativa
        # - Precio fijo de $50.00 en Sellador Especial
        cls.partner = cls.partners.create({
            'name': 'Cliente Comercial Acuerdos',
            'is_customer': True,
        })
        cls.rules.create({
            'partner_id': cls.partner.id,
            'rule_type': 'discount',
            'applied_on': '1_line',
            'line_id': cls.line_decor.id,
            'discount': 15.0,
        })
        cls.rules.create({
            'partner_id': cls.partner.id,
            'rule_type': 'fixed_price',
            'applied_on': '0_product',
            'product_id': cls.prod_fixed.id,
            'fixed_price': 50.0,
        })

        # Programa de lealtad: Promoción 20% de descuento
        cls.program_20pc = cls.env['loyalty.program'].create({
            'name': 'Promoción Gran Venta 20%',
            'program_type': 'promotion',
            'applies_on': 'current',
            'company_id': cls.env.company.id,
            'trigger': 'auto',
            'rule_ids': [(0, 0, {
                'reward_point_amount': 1,
                'reward_point_mode': 'order',
                'minimum_qty': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount_mode': 'percent',
                'discount': 20.0,
                'discount_applicability': 'order',
                'required_points': 1,
            })],
        })
        cls.reward_20pc = cls.program_20pc.reward_ids[0]

        # Programa de regalo: 1 Rodillo Profesional Gratis
        cls.program_free_product = cls.env['loyalty.program'].create({
            'name': 'Regalo Rodillo',
            'program_type': 'promotion',
            'applies_on': 'current',
            'company_id': cls.env.company.id,
            'trigger': 'auto',
            'rule_ids': [(0, 0, {
                'reward_point_amount': 1,
                'reward_point_mode': 'order',
                'minimum_qty': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_id': cls.prod_gift.id,
                'reward_product_qty': 1,
                'required_points': 1,
            })],
        })
        cls.reward_free_product = cls.program_free_product.reward_ids[0]

    def test_01_backend_discount_promo_inline_and_exclusivity(self):
        """Caso 1: Promoción 20% sobre producto con acuerdo comercial de 15%.
        Se aplica directamente a la línea y no crea líneas adicionales de recompensa.
        Al cambiar a 'Descuento de Cliente' en el wizard, se restaura el 15% comercial."""
        order = self.sale_orders.create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.prod_decor.id,
                'product_uom_qty': 2.0,
            })],
        })
        line = order.order_line[0]
        # Verifica que inicialmente aplica el 15% del acuerdo comercial
        self.assertEqual(line.discount, 15.0, "La línea debe iniciar con 15% de descuento por acuerdo comercial.")
        self.assertEqual(line.pricing_rule_type, 'line')

        # Aplicar promoción del 20% mediante el wizard
        order._update_programs_and_rewards()
        wizard = self.env['sale.loyalty.reward.wizard'].with_context(active_id=order.id).create({
            'loyalty_action_type': 'reward',
            'selected_reward_id': self.reward_20pc.id,
        })
        wizard.action_apply_custom()

        # Verificar que la línea tomó el 20% de la promoción directamente en la línea
        self.assertEqual(len(order.order_line), 1, "No debe crearse ninguna línea separada de recompensa.")
        self.assertFalse(order.order_line.filtered('is_reward_line'), "No debe existir ninguna línea is_reward_line.")
        self.assertEqual(line.discount, 20.0, "El descuento de la línea debe ser el 20.0% de la promoción.")
        self.assertEqual(line.pricing_rule_type, 'promo')
        self.assertIn("Promoción", line.pricing_rule_origin)

        # Cambiar de opinión: seleccionar 'Descuento de Cliente' en el wizard
        wizard_discount = self.env['sale.loyalty.reward.wizard'].with_context(active_id=order.id).create({
            'loyalty_action_type': 'discount',
        })
        wizard_discount.action_apply_custom()

        # Verificar que el acuerdo comercial se restableció al 15%
        self.assertEqual(line.discount, 15.0, "Al seleccionar Descuento de Cliente debe restaurarse el 15.0% comercial.")
        self.assertEqual(line.pricing_rule_type, 'line')

    def test_02_fixed_price_strict_protection(self):
        """Caso 2: Los productos con precio fijo pactado ($50.00) no deben recibir
        descuentos promocionales ni alteraciones en su precio unitario acordado."""
        order = self.sale_orders.create({
            'partner_id': self.partner.id,
            'order_line': [
                (0, 0, {
                    'product_id': self.prod_fixed.id,
                    'product_uom_qty': 1.0,
                }),
                (0, 0, {
                    'product_id': self.prod_decor.id,
                    'product_uom_qty': 1.0,
                }),
            ],
        })
        fixed_line = order.order_line.filtered(lambda l: l.product_id == self.prod_fixed)
        decor_line = order.order_line.filtered(lambda l: l.product_id == self.prod_decor)

        self.assertEqual(fixed_line.price_unit, 50.0, "El precio unitario debe ser el precio fijo pactado.")
        self.assertEqual(fixed_line.discount, 0.0)
        self.assertEqual(fixed_line.pricing_rule_type, 'fixed_price')

        # Aplicar promoción del 20%
        order._update_programs_and_rewards()
        wizard = self.env['sale.loyalty.reward.wizard'].with_context(active_id=order.id).create({
            'loyalty_action_type': 'reward',
            'selected_reward_id': self.reward_20pc.id,
        })
        wizard.action_apply_custom()

        # Verificar que la línea con precio fijo NO fue modificada
        self.assertEqual(fixed_line.price_unit, 50.0, "El precio fijo pactado es inviolable.")
        self.assertEqual(fixed_line.discount, 0.0, "La línea con precio fijo no debe recibir descuento promocional.")
        self.assertEqual(fixed_line.pricing_rule_type, 'fixed_price')

        # La línea decorativa sí tomó la promoción
        self.assertEqual(decor_line.discount, 20.0)
        self.assertEqual(decor_line.pricing_rule_type, 'promo')

    def test_03_colorant_protection(self):
        """Caso 3: Los componentes de colorante formulados no deben recibir descuentos de promoción."""
        order = self.sale_orders.create({
            'partner_id': self.partner.id,
            'order_line': [
                (0, 0, {
                    'product_id': self.prod_colorant.id,
                    'product_uom_qty': 10.0,
                }),
                (0, 0, {
                    'product_id': self.prod_decor.id,
                    'product_uom_qty': 1.0,
                }),
            ],
        })
        colorant_line = order.order_line.filtered(lambda l: l.product_id == self.prod_colorant)

        order._update_programs_and_rewards()
        wizard = self.env['sale.loyalty.reward.wizard'].with_context(active_id=order.id).create({
            'loyalty_action_type': 'reward',
            'selected_reward_id': self.reward_20pc.id,
        })
        wizard.action_apply_custom()

        self.assertEqual(colorant_line.discount, 0.0, "Los colorantes formulados no deben recibir descuentos promocionales.")
        self.assertNotEqual(colorant_line.pricing_rule_type, 'promo')

    def test_04_free_product_reward_inline(self):
        """Caso 4: Recompensa de producto gratis (100% descuento).
        - Si el producto ya está en la orden, se bonifica al 100% de descuento en su línea.
        - Si no está, se crea como línea regular a 100% de descuento sin ser is_reward_line."""
        # Subcaso A: Producto ya existe en la orden
        order_a = self.sale_orders.create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.prod_gift.id,
                'product_uom_qty': 1.0,
            })],
        })
        order_a._update_programs_and_rewards()
        wizard_a = self.env['sale.loyalty.reward.wizard'].with_context(active_id=order_a.id).create({
            'loyalty_action_type': 'reward',
            'selected_reward_id': self.reward_free_product.id,
        })
        wizard_a.action_apply_custom()

        self.assertEqual(len(order_a.order_line), 1, "No debe crearse línea extra si el producto ya existía.")
        line_a = order_a.order_line[0]
        self.assertEqual(line_a.discount, 100.0, "La línea existente debe recibir 100% de descuento.")
        self.assertEqual(line_a.pricing_rule_type, 'promo')
        self.assertFalse(line_a.is_reward_line)

        # Subcaso B: Producto no existía en la orden
        order_b = self.sale_orders.create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.prod_decor.id,
                'product_uom_qty': 1.0,
            })],
        })
        order_b._update_programs_and_rewards()
        wizard_b = self.env['sale.loyalty.reward.wizard'].with_context(active_id=order_b.id).create({
            'loyalty_action_type': 'reward',
            'selected_reward_id': self.reward_free_product.id,
        })
        wizard_b.action_apply_custom()

        gift_line = order_b.order_line.filtered(lambda l: l.product_id == self.prod_gift)
        self.assertTrue(gift_line, "Debe haberse creado la línea para el producto de regalo.")
        self.assertEqual(gift_line.discount, 100.0, "La nueva línea debe tener 100% de descuento.")
        self.assertEqual(gift_line.pricing_rule_type, 'promo')
        self.assertFalse(gift_line.is_reward_line, "La nueva línea debe ser una línea regular (is_reward_line=False).")

    def test_05_commercial_discount_restoration_logic(self):
        """Caso 5: Verificación de restauración de acuerdos comerciales del cliente."""
        order = self.sale_orders.create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.prod_decor.id,
                'product_uom_qty': 5.0,
            })],
        })
        line = order.order_line[0]
        self.assertEqual(line.discount, 15.0)

        # Simulamos aplicación de promoción
        line.write({
            'discount': 20.0,
            'pricing_rule_type': 'promo',
            'pricing_rule_origin': 'Promoción: Promo Temporal',
        })
        self.assertEqual(line.discount, 20.0)
        self.assertEqual(line.pricing_rule_type, 'promo')

        # Restauración de acuerdos comerciales (ej. ante descalificación o cambio de cliente)
        order._recompute_pricing_rules()
        self.assertEqual(line.discount, 15.0, "Debe restaurarse el 15.0% de acuerdo comercial.")
        self.assertEqual(line.pricing_rule_type, 'line')
        self.assertIn("Desc. Línea", line.pricing_rule_origin)
