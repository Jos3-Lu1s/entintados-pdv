# -*- coding: utf-8 -*-

from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPartnerDiscountHierarchy(TransactionCase):
    """Pruebas para la jerarquía de acuerdos de precios fijos y descuentos en la ficha del cliente."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partners = cls.env['res.partner']
        cls.rules = cls.env['res.partner.discount.rule']
        cls.templates = cls.env['product.template']
        cls.products = cls.env['product.product']
        cls.schemas = cls.env['tint.schema']
        cls.lines = cls.env['lines.product']
        cls.sale_orders = cls.env['sale.order']

        # Crear esquema y líneas
        cls.schema_a = cls.schemas.create({'name': 'Esquema Decorativo'})
        cls.line_1 = cls.lines.create({
            'name': 'Línea Interiores',
            'scheme': cls.schema_a.id,
        })
        cls.line_2 = cls.lines.create({
            'name': 'Línea Exteriores',
            'scheme': cls.schema_a.id,
        })

        # Productos
        # Producto 1: En Línea 1, Esquema A
        cls.tmpl_1 = cls.templates.create({
            'name': 'Pintura Interior Mate',
            'list_price': 500.0,
            'lines_product_id': cls.line_1.id,
        })
        cls.prod_1 = cls.tmpl_1.product_variant_ids[0]

        # Producto 2: En Línea 2, Esquema A
        cls.tmpl_2 = cls.templates.create({
            'name': 'Pintura Exterior Satinada',
            'list_price': 800.0,
            'lines_product_id': cls.line_2.id,
        })
        cls.prod_2 = cls.tmpl_2.product_variant_ids[0]

        # Producto 3: Sin línea ni esquema asignados (genérico)
        cls.tmpl_3 = cls.templates.create({
            'name': 'Brocha 4 Pulgadas',
            'list_price': 100.0,
        })
        cls.prod_3 = cls.tmpl_3.product_variant_ids[0]

        # Cliente de prueba
        cls.partner = cls.partners.create({
            'name': 'Cliente Comercial VIP',
            'is_customer': True,
            'discount': 0.05,  # 5% Descuento Global fallback
            'phone': '1234567890',
        })

    def test_constraints_unique_and_validations(self):
        """Validar restricciones de integridad del modelo res.partner.discount.rule."""
        # 1. Regla con fixed_price en línea no debe permitirse
        with self.assertRaises(ValidationError):
            self.rules.create({
                'partner_id': self.partner.id,
                'applied_on': '1_line',
                'rule_type': 'fixed_price',
                'line_id': self.line_1.id,
                'fixed_price': 400.0,
            })

        # 2. Descuento negativo o mayor a 100
        with self.assertRaises(ValidationError):
            self.rules.create({
                'partner_id': self.partner.id,
                'applied_on': '0_product',
                'rule_type': 'discount',
                'product_id': self.prod_1.id,
                'discount': 120.0,
            })

        # 3. Precio fijo negativo
        with self.assertRaises(ValidationError):
            self.rules.create({
                'partner_id': self.partner.id,
                'applied_on': '0_product',
                'rule_type': 'fixed_price',
                'product_id': self.prod_1.id,
                'fixed_price': -50.0,
            })

        # 4. Regla válida para producto 1
        rule1 = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 350.0,
        })
        self.assertTrue(rule1.id)

        # 5. Duplicar regla para el mismo producto en el mismo cliente debe fallar
        with self.assertRaises(ValidationError):
            self.rules.create({
                'partner_id': self.partner.id,
                'applied_on': '0_product',
                'rule_type': 'discount',
                'product_id': self.prod_1.id,
                'discount': 10.0,
            })

        # 6. Duplicar regla para la misma línea debe fallar
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '1_line',
            'rule_type': 'discount',
            'line_id': self.line_1.id,
            'discount': 15.0,
        })
        with self.assertRaises(ValidationError):
            self.rules.create({
                'partner_id': self.partner.id,
                'applied_on': '1_line',
                'rule_type': 'discount',
                'line_id': self.line_1.id,
                'discount': 20.0,
            })

    def test_hierarchy_resolution(self):
        """Verificar la jerarquía estricta de 5 niveles en _get_partner_pricing_rule."""
        # Limpiar reglas previas
        self.partner.discount_rule_ids.unlink()

        # Nivel 5: Sin reglas específicas, aplica descuento global (5%)
        rule_p1 = self.partner._get_partner_pricing_rule(self.prod_1)
        self.assertEqual(rule_p1['type'], 'discount')
        self.assertEqual(rule_p1['discount'], 5.0)

        # Nivel 4: Regla en Esquema A (10%)
        rule_scheme = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '2_scheme',
            'rule_type': 'discount',
            'scheme_id': self.schema_a.id,
            'discount': 10.0,
        })
        # Ambos productos del Esquema A ahora toman 10%, brocha genérica toma 5%
        self.assertEqual(self.partner._get_partner_pricing_rule(self.prod_1)['discount'], 10.0)
        self.assertEqual(self.partner._get_partner_pricing_rule(self.prod_2)['discount'], 10.0)
        self.assertEqual(self.partner._get_partner_pricing_rule(self.prod_3)['discount'], 5.0)

        # Nivel 3: Regla en Línea 1 (18%)
        rule_line1 = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '1_line',
            'rule_type': 'discount',
            'line_id': self.line_1.id,
            'discount': 18.0,
        })
        # Prod 1 (Línea 1) toma 18%; Prod 2 (Línea 2) se queda con el 10% del Esquema A
        self.assertEqual(self.partner._get_partner_pricing_rule(self.prod_1)['discount'], 18.0)
        self.assertEqual(self.partner._get_partner_pricing_rule(self.prod_2)['discount'], 10.0)

        # Nivel 2: Descuento en Producto 1 (25%)
        rule_prod1_disc = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.prod_1.id,
            'discount': 25.0,
        })
        # Prod 1 toma 25% (supera a Línea 18% y Esquema 10%)
        self.assertEqual(self.partner._get_partner_pricing_rule(self.prod_1)['discount'], 25.0)

        # Nivel 1: Precio Fijo en Producto 1 ($350.00)
        rule_prod1_disc.unlink()
        rule_prod1_fixed = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 350.0,
        })
        res_fixed = self.partner._get_partner_pricing_rule(self.prod_1)
        self.assertEqual(res_fixed['type'], 'fixed_price')
        self.assertEqual(res_fixed['price'], 350.0)
        self.assertEqual(res_fixed['discount'], 0.0)

    def test_sale_order_line_integration(self):
        """Comprobar cómputo de precio y descuento en sale.order.line."""
        self.partner.discount_rule_ids.unlink()

        # Configurar reglas:
        # Prod 1: Precio Fijo $320.00
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 320.0,
        })
        # Línea 2: Descuento 15% (aplica a Prod 2)
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '1_line',
            'rule_type': 'discount',
            'line_id': self.line_2.id,
            'discount': 15.0,
        })

        order = self.sale_orders.create({
            'partner_id': self.partner.id,
        })

        # Línea 1: Producto 1 con precio fijo
        line1 = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_1.id,
            'product_uom_qty': 2.0,
        })
        self.assertEqual(line1.price_unit, 320.0)
        self.assertEqual(line1.discount, 0.0)

        # Línea 2: Producto 2 con descuento por línea
        line2 = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_2.id,
            'product_uom_qty': 1.0,
        })
        self.assertEqual(line2.price_unit, 800.0)
        self.assertEqual(line2.discount, 15.0)

        # Línea 3: Producto 3 (genérico sin reglas, toma fallback global 5%)
        line3 = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_3.id,
            'product_uom_qty': 1.0,
        })
        self.assertEqual(line3.discount, 5.0)

    def test_pos_session_loading(self):
        """Verificar que el modelo de acuerdos comerciales esté registrado para el POS."""
        pos_session_model = self.env['pos.session']
        models_loaded = pos_session_model._load_pos_data_models(self.env['pos.config'])
        self.assertIn(
            'res.partner.discount.rule',
            models_loaded,
            "res.partner.discount.rule debe estar en _load_pos_data_models para que el POS cargue los acuerdos.",
        )

        # Comprobar campos cargados
        rule_model = self.env['res.partner.discount.rule']
        loaded_fields = rule_model._load_pos_data_fields(False)
        self.assertIn('fixed_price', loaded_fields)
        self.assertIn('discount', loaded_fields)
        self.assertIn('rule_type', loaded_fields)
        self.assertIn('applied_on', loaded_fields)
        self.assertIn('product_id', loaded_fields)
        self.assertIn('line_id', loaded_fields)
        self.assertIn('scheme_id', loaded_fields)

    def test_loyalty_promotion_fixed_price_protection(self):
        """Verificar que las promociones de lealtad no alteren precios fijos pactados ni los descuenten."""
        self.partner.discount_rule_ids.unlink()
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 300.0,
        })
        order = self.sale_orders.create({
            'partner_id': self.partner.id,
        })
        line1 = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_1.id,
            'product_uom_qty': 1.0,
        })
        line2 = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_3.id,
            'product_uom_qty': 1.0,
        })
        self.assertEqual(line1.price_unit, 300.0)
        self.assertEqual(line1.discount, 0.0)
        self.assertEqual(line1.pricing_rule_type, 'fixed_price')
        self.assertEqual(line2.price_unit, 100.0)

        program = self.env['loyalty.program'].create({
            'name': 'Promo 10% Descuento',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'order',
                'minimum_amount': 0.0,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount': 10.0,
                'discount_mode': 'percent',
                'discount_applicability': 'order',
            })],
        })
        reward = program.reward_ids[0]
        coupon = self.env['loyalty.card'].create({
            'program_id': program.id,
            'points': 0,
        })

        reward_vals = order._get_reward_values_discount(reward, coupon)
        self.assertTrue(reward_vals)
        # line2 recibe 5% de descuento comercial base del cliente ($100 * 0.95 = $95 netos)
        self.assertEqual(line2.discount, 5.0)
        # El 10% promocional aplica únicamente sobre la base descontable neta ($95 * 10% = -$9.50)
        self.assertAlmostEqual(reward_vals[0]['price_unit'], -9.5)
        self.assertEqual(line1.price_unit, 300.0)
        self.assertEqual(line1.discount, 0.0)

    def test_sale_order_line_change_product_reactivity(self):
        """Verificar que al cambiar product_id en una línea existente, el precio y descuento se actualicen de inmediato."""
        self.partner.discount_rule_ids.unlink()

        # Prod 1: Precio fijo pactado de $320.00
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 320.0,
        })
        # Línea 2: Descuento 15.0% (aplica a Prod 2, list_price=800.0)
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '1_line',
            'rule_type': 'discount',
            'line_id': self.line_2.id,
            'discount': 15.0,
        })

        order = self.sale_orders.create({
            'partner_id': self.partner.id,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_1.id,
            'product_uom_qty': 1.0,
        })
        self.assertEqual(line.price_unit, 320.0)
        self.assertEqual(line.discount, 0.0)

        # 1. Cambiar producto a Prod 2 (sin precio fijo, pero con descuento por línea del 15%)
        line.product_id = self.prod_2
        line._onchange_product_id()
        self.assertEqual(line.price_unit, 800.0, "Debe restablecer el precio de lista de Prod 2.")
        self.assertEqual(line.discount, 15.0, "Debe calcular el 15% de descuento correspondiente a la Línea 2.")

        # 2. Agregar regla de precio fijo para Prod 3 ($75.00) y cambiar a Prod 3
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_3.id,
            'fixed_price': 75.0,
        })
        line.product_id = self.prod_3
        line._onchange_product_id()
        self.assertEqual(line.price_unit, 75.0, "Debe aplicar el precio fijo pactado de Prod 3.")
        self.assertEqual(line.discount, 0.0, "El descuento debe ser 0.0% para líneas con precio fijo.")

    def test_sale_order_change_partner_reactivity(self):
        """Verificar que al cambiar de contacto en la cabecera, todas las líneas recalculen precio y descuento."""
        self.partner.discount_rule_ids.unlink()

        # Configurar Cliente A (self.partner): Prod 1 precio fijo $320, descuento global 5%
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 320.0,
        })

        # Crear Cliente B: Prod 1 precio fijo $450, descuento global 10%
        partner_b = self.partners.create({
            'name': 'Cliente B Mayorista',
            'is_customer': True,
            'discount': 0.10,  # 10%
            'phone': '9876543210',
        })
        self.rules.create({
            'partner_id': partner_b.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 450.0,
        })

        order = self.sale_orders.create({
            'partner_id': self.partner.id,
        })
        line1 = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_1.id,
            'product_uom_qty': 1.0,
        })
        line2 = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_3.id,
            'product_uom_qty': 1.0,
        })

        # Verificar valores iniciales con Cliente A
        self.assertEqual(line1.price_unit, 320.0)
        self.assertEqual(line1.discount, 0.0)
        self.assertEqual(line2.price_unit, 100.0)  # list_price de prod_3
        self.assertEqual(line2.discount, 5.0)     # fallback de Cliente A

        # Cambiar a Cliente B
        order.partner_id = partner_b
        order._onchange_partner_id_entintados_rules()

        # Verificar que ambas líneas se actualizaron a las condiciones de Cliente B
        self.assertEqual(line1.price_unit, 450.0, "La línea 1 debe actualizarse al precio fijo acordado con Cliente B.")
        self.assertEqual(line1.discount, 0.0)
        self.assertEqual(line2.price_unit, 100.0)
        self.assertEqual(line2.discount, 10.0, "La línea 2 debe actualizarse al fallback global del 10% de Cliente B.")

    def test_commercial_agreement_change_does_not_mutate_existing_records(self):
        """Verificar que cambiar el precio o descuento en los acuerdos comerciales del cliente
        NO modifique las cotizaciones u órdenes ya existentes, ni al modificar cantidades en líneas guardadas,
        pero sí aplique a las cotizaciones y líneas que se creen después."""
        self.partner.discount_rule_ids.unlink()

        # Configurar acuerdo inicial para Prod 1: Precio fijo $300.00
        rule = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 300.0,
        })

        # 1. Crear cotización previa
        order_previa = self.sale_orders.create({
            'partner_id': self.partner.id,
        })
        line_previa = self.env['sale.order.line'].create({
            'order_id': order_previa.id,
            'product_id': self.prod_1.id,
            'product_uom_qty': 1.0,
        })
        self.assertEqual(line_previa.price_unit, 300.0, "La cotización previa debe tomar el precio pactado inicial de $300.")
        self.assertEqual(line_previa.discount, 0.0)

        # 2. Modificar el acuerdo comercial en la ficha del cliente a $420.00
        rule.write({'fixed_price': 420.0})
        self.env.flush_all()
        self.env.invalidate_all()

        # 3. Validar que la cotización previa conserva estrictamente su precio pactado inicial ($300.00)
        line_previa_reloaded = self.env['sale.order.line'].browse(line_previa.id)
        self.assertEqual(
            line_previa_reloaded.price_unit,
            300.0,
            "Al modificar el acuerdo del cliente, las cotizaciones existentes NO deben mutar su precio unitario.",
        )

        # 4. Modificar la cantidad en la cotización previa (de 1.0 a 5.0) y verificar que permanece congelada en $300.00
        line_previa_reloaded.product_uom_qty = 5.0
        line_previa_reloaded._compute_price_unit()
        self.assertEqual(
            line_previa_reloaded.price_unit,
            300.0,
            "Al modificar la cantidad en una línea existente de cotización en borrador, se debe conservar el precio histórico.",
        )

        # 5. Crear una NUEVA cotización posterior y verificar que sí toma el nuevo precio pactado ($420.00)
        order_nueva = self.sale_orders.create({
            'partner_id': self.partner.id,
        })
        line_nueva = self.env['sale.order.line'].create({
            'order_id': order_nueva.id,
            'product_id': self.prod_1.id,
            'product_uom_qty': 1.0,
        })
        self.assertEqual(
            line_nueva.price_unit,
            420.0,
            "Las cotizaciones o líneas creadas después del cambio deben tomar el nuevo acuerdo de $420.",
        )
        self.assertEqual(line_nueva.discount, 0.0)

    def test_pricing_rule_origin_metadata_and_labels(self):
        """Validar que _get_partner_pricing_rule genere origin_type y origin_label correctos para cada nivel."""
        self.partner.discount_rule_ids.unlink()

        # Fallback sin reglas pero con descuento global 5%
        res_global = self.partner._get_partner_pricing_rule(self.prod_3)
        self.assertEqual(res_global['origin_type'], 'global')
        self.assertEqual(res_global['origin_label'], 'Desc. Global Cliente (5.0%)')
        self.assertEqual(res_global['discount'], 5.0)

        # Nivel 4: Esquema (10%)
        rule_scheme = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '2_scheme',
            'rule_type': 'discount',
            'scheme_id': self.schema_a.id,
            'discount': 10.0,
        })
        res_scheme = self.partner._get_partner_pricing_rule(self.prod_2)
        self.assertEqual(res_scheme['origin_type'], 'scheme')
        self.assertEqual(res_scheme['origin_label'], f"Desc. Esquema: {self.schema_a.name} (10.0%)")
        self.assertEqual(res_scheme['rule'].id, rule_scheme.id)

        # Nivel 3: Línea (18%)
        rule_line = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '1_line',
            'rule_type': 'discount',
            'line_id': self.line_1.id,
            'discount': 18.0,
        })
        res_line = self.partner._get_partner_pricing_rule(self.prod_1)
        self.assertEqual(res_line['origin_type'], 'line')
        self.assertEqual(res_line['origin_label'], f"Desc. Línea: {self.line_1.name} (18.0%)")
        self.assertEqual(res_line['rule'].id, rule_line.id)

        # Nivel 2: Producto (25%)
        rule_prod = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.prod_1.id,
            'discount': 25.0,
        })
        res_prod = self.partner._get_partner_pricing_rule(self.prod_1)
        self.assertEqual(res_prod['origin_type'], 'product')
        self.assertEqual(res_prod['origin_label'], "Desc. Producto (25.0%)")
        self.assertEqual(res_prod['rule'].id, rule_prod.id)

        # Nivel 1: Precio Fijo ($320.0)
        rule_prod.unlink()
        rule_fixed = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 320.0,
        })
        res_fixed = self.partner._get_partner_pricing_rule(self.prod_1)
        self.assertEqual(res_fixed['origin_type'], 'fixed_price')
        self.assertEqual(res_fixed['origin_label'], "Precio Fijo")
        self.assertEqual(res_fixed['price'], 320.0)
        self.assertEqual(res_fixed['rule'].id, rule_fixed.id)

        # Cliente sin descuento y producto genérico: None
        cliente_neutro = self.partners.create({
            'name': 'Cliente Neutro',
            'is_customer': True,
            'discount': 0.0,
        })
        res_none = cliente_neutro._get_partner_pricing_rule(self.prod_3)
        self.assertEqual(res_none['origin_type'], 'none')
        self.assertEqual(res_none['origin_label'], '')
        self.assertEqual(res_none['discount'], 0.0)

    def test_sale_order_line_origin_persistence_and_reactivity(self):
        """Validar persistencia y reactividad de pricing_rule_* en sale.order.line."""
        self.partner.discount_rule_ids.unlink()

        # Configurar regla de precio fijo para prod 1 y regla de línea para línea 2 (prod 2)
        fixed_rule = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 350.0,
        })
        line_rule = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '1_line',
            'rule_type': 'discount',
            'line_id': self.line_2.id,
            'discount': 12.0,
        })

        order = self.sale_orders.create({
            'partner_id': self.partner.id,
        })
        l1 = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_1.id,
            'product_uom_qty': 1.0,
        })
        l2 = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_2.id,
            'product_uom_qty': 1.0,
        })
        l3 = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_3.id,
            'product_uom_qty': 1.0,
        })

        # Comprobar línea 1 (Precio fijo)
        self.assertEqual(l1.pricing_rule_type, 'fixed_price')
        self.assertEqual(l1.pricing_rule_origin, 'Precio Fijo')
        self.assertEqual(l1.pricing_rule_id.id, fixed_rule.id)
        self.assertEqual(l1.price_unit, 350.0)
        self.assertEqual(l1.discount, 0.0)

        # Comprobar línea 2 (Línea de producto)
        self.assertEqual(l2.pricing_rule_type, 'line')
        self.assertEqual(l2.pricing_rule_origin, f"Desc. Línea: {self.line_2.name} (12.0%)")
        self.assertEqual(l2.pricing_rule_id.id, line_rule.id)
        self.assertEqual(l2.discount, 12.0)

        # Comprobar línea 3 (Descuento Global fallback 5%)
        self.assertEqual(l3.pricing_rule_type, 'global')
        self.assertEqual(l3.pricing_rule_origin, 'Desc. Global Cliente (5.0%)')
        self.assertFalse(l3.pricing_rule_id)
        self.assertEqual(l3.discount, 5.0)

        # Reactividad ante cambio de producto en línea 1 (cambia de prod_1 a prod_2)
        l1.product_id = self.prod_2
        l1._onchange_product_id()
        self.assertEqual(l1.pricing_rule_type, 'line')
        self.assertEqual(l1.pricing_rule_origin, f"Desc. Línea: {self.line_2.name} (12.0%)")
        self.assertEqual(l1.pricing_rule_id.id, line_rule.id)

        # Reactividad ante cambio de contacto (cambia a Cliente C con descuento global 8%)
        cliente_c = self.partners.create({
            'name': 'Cliente C',
            'is_customer': True,
            'discount': 0.08,
        })
        order.partner_id = cliente_c
        order._onchange_partner_id_entintados_rules()
        self.assertEqual(l2.pricing_rule_type, 'global')
        self.assertEqual(l2.pricing_rule_origin, 'Desc. Global Cliente (8.0%)')
        self.assertEqual(l3.pricing_rule_type, 'global')
        self.assertEqual(l3.pricing_rule_origin, 'Desc. Global Cliente (8.0%)')

    def test_pos_order_line_fields_persistence(self):
        """Validar persistencia de pricing_rule_* en pos.order.line y exposición en _load_pos_data_fields."""
        fixed_rule = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 310.0,
        })

        # Verificar que pos.order.line carga los campos para el POS
        loaded_fields = self.env['pos.order.line']._load_pos_data_fields(False)
        self.assertIn('pricing_rule_type', loaded_fields)
        self.assertIn('pricing_rule_origin', loaded_fields)
        self.assertIn('pricing_rule_id', loaded_fields)

    def test_sent_quotation_change_partner_updates_pricing_rule_origin(self):
        """Reproducir bug: en cotización 'sent', cambiar partner_id debe actualizar precio, descuento y pricing_rule_origin."""
        self.partner.discount_rule_ids.unlink()
        fixed_rule = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 350.0,
        })
        order = self.sale_orders.create({
            'partner_id': self.partner.id,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_1.id,
            'product_uom_qty': 1.0,
        })
        self.assertEqual(line.price_unit, 350.0)
        self.assertEqual(line.pricing_rule_origin, 'Precio Fijo')

        # Pasar la cotización a estado 'sent'
        order.state = 'sent'

        # Crear partner B con descuento global del 10%
        partner_b = self.partners.create({
            'name': 'Cliente B Sent',
            'is_customer': True,
            'discount': 0.10,
        })

        # Cambiar el cliente en la cotización en estado 'sent' (como lo hace el guardado en UI)
        order.write({'partner_id': partner_b.id})

        # Forzar recarga desde la base de datos para asegurar que se persistió en PostgreSQL
        line.invalidate_recordset()

        # Verificar que el precio se restablece al de lista (500), el descuento es 10% y el badge refleja 'Desc. Global Cliente (10.0%)'
        self.assertEqual(line.price_unit, 500.0, "El precio unitario debe restablecerse al precio de lista.")
        self.assertEqual(line.discount, 10.0, "El descuento debe actualizarse al 10% de partner_b.")
        self.assertEqual(line.pricing_rule_origin, 'Desc. Global Cliente (10.0%)', "El badge de Origen Acuerdo no debe desaparecer ni revertirse al valor anterior.")

    def test_sent_quotation_change_partner_with_onchange_and_save(self):
        """Simular el ciclo completo del cliente web en estado 'sent': onchange + save con líneas."""
        self.partner.discount_rule_ids.unlink()
        fixed_rule = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 340.0,
        })
        order = self.sale_orders.create({
            'partner_id': self.partner.id,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_1.id,
            'product_uom_qty': 1.0,
        })
        self.assertEqual(line.price_unit, 340.0)
        self.assertEqual(line.pricing_rule_origin, 'Precio Fijo')

        order.state = 'sent'

        # Partner C con precio fijo diferente para prod_1 ($410.0)
        partner_c = self.partners.create({
            'name': 'Cliente C Sent',
            'is_customer': True,
        })
        self.rules.create({
            'partner_id': partner_c.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 410.0,
        })

        # 1. Simulación Onchange en UI
        order.partner_id = partner_c
        order._onchange_partner_id_entintados_rules()
        self.assertEqual(line.price_unit, 410.0)
        self.assertEqual(line.pricing_rule_origin, 'Precio Fijo')

        # 2. Simulación de Guardar (Save) desde el cliente web
        order.write({
            'partner_id': partner_c.id,
            'order_line': [(1, line.id, {'price_unit': line.price_unit})],
        })

        line.invalidate_recordset()
        self.assertEqual(line.price_unit, 410.0, "El precio acordado con partner_c ($410) debe persistir.")
        self.assertEqual(line.pricing_rule_origin, 'Precio Fijo', "El badge 'Precio Fijo' debe persistir.")
        self.assertEqual(line.pricing_rule_type, 'fixed_price')

    def test_sent_quotation_change_product_on_existing_line(self):
        """Validar cambio de producto en línea existente en cotización 'sent' con persistencia."""
        self.partner.discount_rule_ids.unlink()
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 330.0,
        })
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '1_line',
            'rule_type': 'discount',
            'line_id': self.line_2.id,
            'discount': 15.0,
        })
        order = self.sale_orders.create({
            'partner_id': self.partner.id,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.prod_1.id,
            'product_uom_qty': 1.0,
        })
        order.state = 'sent'
        self.assertEqual(line.price_unit, 330.0)
        self.assertEqual(line.pricing_rule_origin, 'Precio Fijo')

        # Cambiar de prod_1 a prod_2 en la UI (onchange)
        line.product_id = self.prod_2
        line._onchange_product_id()
        self.assertEqual(line.price_unit, 800.0)
        self.assertEqual(line.discount, 15.0)
        self.assertEqual(line.pricing_rule_origin, f"Desc. Línea: {self.line_2.name} (15.0%)")

        # Guardar en base de datos
        order.write({
            'order_line': [(1, line.id, {'product_id': self.prod_2.id, 'price_unit': 800.0})],
        })
        line.invalidate_recordset()
        self.assertEqual(line.price_unit, 800.0)
        self.assertEqual(line.discount, 15.0)
        self.assertEqual(line.pricing_rule_origin, f"Desc. Línea: {self.line_2.name} (15.0%)")

    def _create_confirmed_order(self, partner, product, qty=1.0):
        order = self.sale_orders.create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': qty})],
        })
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        return order

    def _add_line_with_form(self, order, product, qty=1.0):
        previous_lines = order.order_line
        with Form(order) as order_form:
            with order_form.order_line.new() as line_form:
                line_form.product_id = product
                line_form.product_uom_qty = qty
        return order.order_line - previous_lines

    def test_confirmed_order_new_line_without_agreement(self):
        """Una línea nueva en un pedido confirmado toma el precio de tarifa (no 0)."""
        partner = self.partners.create({'name': 'Cliente Sin Acuerdos', 'is_customer': True})
        order = self._create_confirmed_order(partner, self.prod_1)

        new_line = self._add_line_with_form(order, self.prod_3)

        self.assertEqual(new_line.price_unit, 100.0)
        self.assertEqual(new_line.discount, 0.0)
        self.assertEqual(new_line.pricing_rule_type, 'none')
        self.assertEqual(order.amount_untaxed, 600.0)
        self.assertEqual(new_line._prepare_invoice_line()['price_unit'], 100.0)

    def test_confirmed_order_new_line_fixed_price(self):
        """Una línea nueva en un pedido confirmado aplica el precio fijo vigente del cliente."""
        self.partner.discount_rule_ids.unlink()
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_3.id,
            'fixed_price': 70.0,
        })
        order = self._create_confirmed_order(self.partner, self.prod_1)

        new_line = self._add_line_with_form(order, self.prod_3)

        self.assertEqual(new_line.price_unit, 70.0)
        self.assertEqual(new_line.discount, 0.0)
        self.assertEqual(new_line.pricing_rule_type, 'fixed_price')
        self.assertEqual(new_line.pricing_rule_origin, 'Precio Fijo')
        self.assertEqual(new_line._prepare_invoice_line()['price_unit'], 70.0)

    def test_confirmed_order_new_line_discount_rules(self):
        """Una línea nueva en un pedido confirmado aplica el descuento vigente de cada nivel."""
        cases = [
            ('product', {'applied_on': '0_product', 'product_id': self.prod_3.id, 'discount': 10.0},
             self.prod_3, 100.0, 10.0),
            ('line', {'applied_on': '1_line', 'line_id': self.line_1.id, 'discount': 12.0},
             self.prod_1, 500.0, 12.0),
            ('scheme', {'applied_on': '2_scheme', 'scheme_id': self.schema_a.id, 'discount': 8.0},
             self.prod_2, 800.0, 8.0),
            ('global', None, self.prod_3, 100.0, 5.0),
        ]
        for origin_type, rule_vals, product, price, discount in cases:
            with self.subTest(origin_type=origin_type):
                self.partner.discount_rule_ids.unlink()
                if rule_vals:
                    self.rules.create(dict(rule_vals, partner_id=self.partner.id, rule_type='discount'))
                order = self._create_confirmed_order(self.partner, self.prod_1)

                new_line = self._add_line_with_form(order, product)

                self.assertEqual(new_line.price_unit, price)
                self.assertAlmostEqual(new_line.discount, discount)
                self.assertEqual(new_line.pricing_rule_type, origin_type)

    def test_confirmed_order_existing_lines_frozen(self):
        """Al agregar una línea a un pedido confirmado, las líneas previas conservan su acuerdo
        aunque el acuerdo del cliente haya cambiado; la nueva toma el acuerdo vigente."""
        self.partner.discount_rule_ids.unlink()
        rule = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 300.0,
        })
        order = self._create_confirmed_order(self.partner, self.prod_1)
        old_line = order.order_line
        self.assertEqual(old_line.price_unit, 300.0)

        rule.write({'fixed_price': 420.0})
        new_line = self._add_line_with_form(order, self.prod_1)

        self.assertEqual(old_line.price_unit, 300.0)
        self.assertEqual(new_line.price_unit, 420.0)

        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                line_form.product_uom_qty = 5.0
        self.assertEqual(old_line.product_uom_qty, 5.0)
        self.assertEqual(old_line.price_unit, 300.0)
        self.assertEqual(old_line.pricing_rule_type, 'fixed_price')

    def _create_volume_pricelist(self):
        return self.env['product.pricelist'].create({
            'name': 'Tarifa Volumen',
            'item_ids': [(0, 0, {
                'applied_on': '0_product_variant',
                'product_id': self.prod_3.id,
                'compute_price': 'fixed',
                'fixed_price': 80.0,
                'min_quantity': 10.0,
            })],
        })

    def _create_saved_quotation(self, partner, pricelist, product, qty=1.0):
        order = self.sale_orders.create({
            'partner_id': partner.id,
            'pricelist_id': pricelist.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': qty})],
        })
        return order, order.order_line

    def test_saved_line_qty_change_recomputes_pricelist(self):
        """Cambiar la cantidad de una línea guardada recalcula el precio por volumen de la tarifa."""
        partner = self.partners.create({'name': 'Cliente Volumen', 'is_customer': True})
        pricelist = self._create_volume_pricelist()

        order, line = self._create_saved_quotation(partner, pricelist, self.prod_3)
        self.assertEqual(line.price_unit, 100.0)
        line.write({'product_uom_qty': 12.0})
        self.assertEqual(line.price_unit, 80.0)
        self.assertEqual(line.price_subtotal, 960.0)

        order, line = self._create_saved_quotation(partner, pricelist, self.prod_3)
        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                line_form.product_uom_qty = 15.0
        self.assertEqual(line.price_unit, 80.0)
        self.assertEqual(line.price_subtotal, 1200.0)

    def test_saved_line_uom_change_recomputes_price(self):
        """Cambiar la UdM de una línea guardada convierte el precio de la tarifa."""
        partner = self.partners.create({'name': 'Cliente UdM', 'is_customer': True})
        pricelist = self.env['product.pricelist'].create({'name': 'Tarifa Lista'})
        order, line = self._create_saved_quotation(partner, pricelist, self.prod_3)

        line.write({'product_uom_id': self.env.ref('uom.product_uom_dozen').id})
        self.assertEqual(line.price_unit, 1200.0)

    def test_saved_line_qty_change_keeps_frozen_agreement_and_manual_price(self):
        """Al cambiar la cantidad, el precio fijo del acuerdo y el precio manual se conservan."""
        pricelist = self._create_volume_pricelist()
        self.partner.discount_rule_ids.unlink()
        rule = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_3.id,
            'fixed_price': 70.0,
        })
        order, line = self._create_saved_quotation(self.partner, pricelist, self.prod_3)
        self.assertEqual(line.price_unit, 70.0)
        rule.write({'fixed_price': 65.0})
        line.write({'product_uom_qty': 12.0})
        self.assertEqual(line.price_unit, 70.0)
        self.assertEqual(line.pricing_rule_type, 'fixed_price')

        partner = self.partners.create({'name': 'Cliente Manual', 'is_customer': True})
        order, line = self._create_saved_quotation(partner, pricelist, self.prod_3)
        line.write({'price_unit': 55.0})
        line.write({'product_uom_qty': 12.0})
        self.assertEqual(line.price_unit, 55.0)

    def test_saved_line_qty_change_after_partner_change_uses_new_partner(self):
        """Cambiar el cliente sin guardar y luego la cantidad no devuelve la línea al acuerdo
        del cliente anterior, ni en pantalla ni al guardar."""
        self.partner.discount_rule_ids.unlink()
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_1.id,
            'fixed_price': 350.0,
        })
        partner_b = self.partners.create({
            'name': 'Cliente B Global',
            'is_customer': True,
            'discount': 0.10,
        })
        self.env.user.group_ids |= self.env.ref('sale.group_discount_per_so_line')
        cases = [
            (self.partner, partner_b, (500.0, 10.0, 'Desc. Global Cliente (10.0%)')),
            (partner_b, self.partner, (350.0, 0.0, 'Precio Fijo')),
        ]
        for partner_from, partner_to, expected in cases:
            with self.subTest(partner_from=partner_from.name, partner_to=partner_to.name):
                order = self.sale_orders.create({
                    'partner_id': partner_from.id,
                    'order_line': [(0, 0, {'product_id': self.prod_1.id, 'product_uom_qty': 1.0})],
                })
                line = order.order_line
                with Form(order) as order_form:
                    order_form.partner_id = partner_to
                    with order_form.order_line.edit(0) as line_form:
                        line_form.product_uom_qty = 2.0
                        self.assertEqual(
                            (line_form.price_unit, line_form.discount, line_form.pricing_rule_origin),
                            expected,
                        )
                self.assertEqual((line.price_unit, line.discount, line.pricing_rule_origin), expected)

    def test_sent_quotation_change_partner_persists_new_pricelist(self):
        """En cotización 'sent', guardar un cambio de cliente sin enviar pricelist_id (el campo es
        readonly en la vista) persiste la tarifa del nuevo cliente y recalcula con ella."""
        self.env.user.group_ids |= self.env.ref('product.group_product_pricelist')
        pricelist_a = self.env['product.pricelist'].create({'name': 'Tarifa A'})
        pricelist_b = self.env['product.pricelist'].create({
            'name': 'Tarifa B',
            'item_ids': [(0, 0, {
                'applied_on': '0_product_variant',
                'product_id': self.prod_1.id,
                'compute_price': 'fixed',
                'fixed_price': 80.0,
            })],
        })
        pricelist_x = self.env['product.pricelist'].create({'name': 'Tarifa X'})
        partner_a = self.partners.create({'name': 'Cliente Tarifa A', 'is_customer': True})
        partner_b = self.partners.create({'name': 'Cliente Tarifa B', 'is_customer': True})
        partner_a.property_product_pricelist = pricelist_a
        partner_b.property_product_pricelist = pricelist_b

        def create_sent_order():
            order = self.sale_orders.create({
                'partner_id': partner_a.id,
                'order_line': [(0, 0, {'product_id': self.prod_1.id, 'product_uom_qty': 1.0})],
            })
            order.state = 'sent'
            self.assertEqual(order.pricelist_id, pricelist_a)
            self.assertEqual(order.order_line.price_unit, 500.0)
            return order

        with self.subTest('tarifa del nuevo cliente'):
            order = create_sent_order()
            vals = {'partner_id': partner_b.id}
            order.write(vals)
            self.assertEqual(vals, {'partner_id': partner_b.id}, "write no debe mutar vals.")
            order.invalidate_recordset()
            order.order_line.invalidate_recordset()
            self.assertEqual(order.pricelist_id, pricelist_b)
            self.assertEqual(order.order_line.price_unit, 80.0)

        with self.subTest('cliente sin tarifa conserva la actual'):
            order = create_sent_order()
            partner_c = self.partners.create({'name': 'Cliente Sin Tarifa', 'is_customer': True})
            Pricelist = self.env.registry['product.pricelist']
            with patch.object(Pricelist, '_get_partner_pricelist_multi',
                              lambda self, partner_ids: {pid: self.browse() for pid in partner_ids}):
                partner_c.invalidate_recordset(['property_product_pricelist'])
                self.assertFalse(partner_c.property_product_pricelist)
                order.write({'partner_id': partner_c.id})
            order.invalidate_recordset()
            self.assertEqual(order.pricelist_id, pricelist_a)

        with self.subTest('pricelist_id explícito tiene prioridad'):
            order = create_sent_order()
            order.write({'partner_id': partner_b.id, 'pricelist_id': pricelist_x.id})
            order.invalidate_recordset()
            self.assertEqual(order.pricelist_id, pricelist_x)

        with self.subTest('pedido confirmado no cambia de tarifa'):
            order = self._create_confirmed_order(partner_a, self.prod_1)
            order.write({'partner_id': partner_b.id})
            order.invalidate_recordset()
            self.assertEqual(order.pricelist_id, pricelist_a)

    # --- Convivencia tarifa × acuerdo comercial (SPEC 02) ---

    def _enable_line_discounts(self):
        self.env.user.group_ids |= self.env.ref('product.group_product_pricelist')
        self.env.user.group_ids |= self.env.ref('sale.group_discount_per_so_line')

    def _create_percentage_pricelist(self, name='Tarifa -20%', items=None):
        """Tarifa con reglas `percentage`; por defecto −20 % global."""
        items = items or [{'applied_on': '3_global', 'percent_price': 20.0}]
        return self.env['product.pricelist'].create({
            'name': name,
            'item_ids': [(0, 0, dict(item, compute_price='percentage')) for item in items],
        })

    def _create_pricelist_partner(self, name, pricelist):
        partner = self.partners.create({'name': name, 'is_customer': True, 'discount': 0.0})
        partner.property_product_pricelist = pricelist
        return partner

    def _line_values(self, line):
        """(price_unit, % tarifa, % acuerdo, discount combinado)."""
        return (line.price_unit, line.pricelist_discount, line.agreement_discount, line.discount)

    def _line_badge(self, line):
        return (line.pricing_rule_type, line.pricing_rule_origin)

    def test_update_prices_keeps_agreement_discount(self):
        """AUD-0021: "Actualizar precios" no borra el descuento del acuerdo y reaplica el vigente."""
        self._enable_line_discounts()
        partner_a = self._create_pricelist_partner(
            'Cliente Tarifa Lista', self.env['product.pricelist'].create({'name': 'Tarifa Lista'}))
        partner_b = self._create_pricelist_partner('Cliente Tarifa -20', self._create_percentage_pricelist())
        rule = self.rules.create({
            'partner_id': partner_b.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.prod_3.id,
            'discount': 10.0,
        })
        order = self.sale_orders.create({
            'partner_id': partner_a.id,
            'order_line': [(0, 0, {'product_id': self.prod_3.id, 'product_uom_qty': 1.0})],
        })
        line = order.order_line
        with Form(order) as order_form:
            order_form.partner_id = partner_b

        order.action_update_prices()
        self.assertEqual(self._line_values(line), (100.0, 20.0, 10.0, 28.0))
        self.assertEqual(self._line_badge(line), ('product', 'Desc. Producto (10.0%)'))
        self.assertAlmostEqual(line.price_subtotal, 72.0)

        rule.write({'discount': 15.0})
        order.action_update_prices()
        self.assertEqual(self._line_values(line), (100.0, 20.0, 15.0, 32.0))
        self.assertEqual(self._line_badge(line), ('product', 'Desc. Producto (15.0%)'))

    def test_pricelist_discount_combined_with_agreement_discount(self):
        """Tarifa −20 % y acuerdo 10 %: el discount combina ambos en cascada con desglose visible."""
        self._enable_line_discounts()
        partner = self._create_pricelist_partner('Cliente Tarifa y Acuerdo', self._create_percentage_pricelist())
        self.rules.create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.prod_3.id,
            'discount': 10.0,
        })
        Item = self.env.registry['product.pricelist.item']
        cases = [
            (True, (100.0, 20.0, 10.0, 28.0)),
            (False, (80.0, 0.0, 10.0, 10.0)),
        ]
        for discount_feature, expected in cases:
            with self.subTest(group_discount_per_so_line=discount_feature), \
                    patch.object(Item, '_is_discount_feature_enabled', lambda self, v=discount_feature: v):
                order = self.sale_orders.create({
                    'partner_id': partner.id,
                    'order_line': [(0, 0, {'product_id': self.prod_3.id, 'product_uom_qty': 1.0})],
                })
                line = order.order_line
                self.assertEqual(self._line_values(line), expected)
                self.assertEqual(self._line_badge(line), ('product', 'Desc. Producto (10.0%)'))
                self.assertAlmostEqual(line.price_subtotal, 72.0)

    def test_pricelist_discount_kept_without_agreement(self):
        """Sin acuerdo aplicable (o producto servicio) la línea lleva solo el % de la tarifa."""
        self._enable_line_discounts()
        pricelist = self._create_percentage_pricelist()
        partner = self._create_pricelist_partner('Cliente Solo Tarifa', pricelist)
        service = self.templates.create({
            'name': 'Servicio de Igualación',
            'type': 'service',
            'list_price': 100.0,
        }).product_variant_ids[0]
        self.partner.property_product_pricelist = pricelist
        cases = [
            ('producto sin acuerdo', partner, self.prod_3),
            ('servicio con acuerdo global', self.partner, service),
        ]
        for label, customer, product in cases:
            with self.subTest(label):
                order = self.sale_orders.create({
                    'partner_id': customer.id,
                    'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1.0})],
                })
                line = order.order_line
                self.assertEqual(self._line_values(line), (100.0, 20.0, 0.0, 20.0))
                self.assertEqual(self._line_badge(line), ('none', ''))
                self.assertAlmostEqual(line.price_subtotal, 80.0)

        with self.subTest('write de product_id sin acuerdo'):
            order = self.sale_orders.create({
                'partner_id': partner.id,
                'order_line': [(0, 0, {'product_id': self.prod_1.id, 'product_uom_qty': 1.0})],
            })
            line = order.order_line
            line.write({'product_id': self.prod_3.id})
            self.assertEqual(self._line_values(line), (100.0, 20.0, 0.0, 20.0))
            self.assertEqual(self._line_badge(line), ('none', ''))

    def test_fixed_price_agreement_overrides_pricelist(self):
        """El precio fijo del acuerdo gana sobre la tarifa y deja ambos descuentos en 0."""
        self._enable_line_discounts()
        partner = self._create_pricelist_partner('Cliente Precio Fijo', self._create_percentage_pricelist())
        self.rules.create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.prod_3.id,
            'fixed_price': 70.0,
        })
        order = self.sale_orders.create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': self.prod_3.id, 'product_uom_qty': 1.0})],
        })
        line = order.order_line
        self.assertEqual(self._line_values(line), (70.0, 0.0, 0.0, 0.0))
        self.assertEqual(self._line_badge(line), ('fixed_price', 'Precio Fijo'))

    def test_saved_line_qty_change_keeps_agreement_and_follows_pricelist(self):
        """Línea guardada con acuerdo 10 %: al subir de escalón el % de la tarifa se recalcula y
        el del acuerdo se conserva."""
        self._enable_line_discounts()
        pricelist = self._create_percentage_pricelist(items=[{
            'applied_on': '0_product_variant',
            'product_id': self.prod_3.id,
            'percent_price': 20.0,
            'min_quantity': 10.0,
        }])
        partner = self._create_pricelist_partner('Cliente Acuerdo Volumen', pricelist)
        self.rules.create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.prod_3.id,
            'discount': 10.0,
        })
        badge = ('product', 'Desc. Producto (10.0%)')

        with self.subTest('write ORM'):
            order, line = self._create_saved_quotation(partner, pricelist, self.prod_3)
            self.assertEqual(self._line_values(line), (100.0, 0.0, 10.0, 10.0))
            line.write({'product_uom_qty': 12.0})
            self.assertEqual(self._line_values(line), (100.0, 20.0, 10.0, 28.0))
            self.assertEqual(self._line_badge(line), badge)

        with self.subTest('Form'):
            order, line = self._create_saved_quotation(partner, pricelist, self.prod_3)
            with Form(order) as order_form:
                with order_form.order_line.edit(0) as line_form:
                    line_form.product_uom_qty = 12.0
            self.assertEqual(self._line_values(line), (100.0, 20.0, 10.0, 28.0))
            self.assertEqual(self._line_badge(line), badge)

    def test_saved_line_qty_change_without_agreement_follows_pricelist(self):
        """Línea guardada sin acuerdo: al subir de escalón, el descuento sigue a la tarifa."""
        self._enable_line_discounts()
        pricelist = self._create_percentage_pricelist(items=[
            {'applied_on': '0_product_variant', 'product_id': self.prod_3.id,
             'percent_price': 20.0, 'min_quantity': 10.0},
            {'applied_on': '0_product_variant', 'product_id': self.prod_3.id,
             'percent_price': 10.0, 'min_quantity': 1.0},
        ])
        partner = self._create_pricelist_partner('Cliente Escalones', pricelist)

        with self.subTest('write ORM'):
            order, line = self._create_saved_quotation(partner, pricelist, self.prod_3)
            self.assertEqual(self._line_values(line), (100.0, 10.0, 0.0, 10.0))
            line.write({'product_uom_qty': 12.0})
            self.assertEqual(self._line_values(line), (100.0, 20.0, 0.0, 20.0))
            self.assertEqual(self._line_badge(line), ('none', ''))

        with self.subTest('Form'):
            order, line = self._create_saved_quotation(partner, pricelist, self.prod_3)
            with Form(order) as order_form:
                with order_form.order_line.edit(0) as line_form:
                    line_form.product_uom_qty = 12.0
            self.assertEqual(self._line_values(line), (100.0, 20.0, 0.0, 20.0))
            self.assertEqual(self._line_badge(line), ('none', ''))

    def test_manual_discount_clears_breakdown(self):
        """Un discount escrito por código manda: limpia el desglose y el badge del acuerdo."""
        self._enable_line_discounts()
        partner = self._create_pricelist_partner('Cliente Descuento Manual', self._create_percentage_pricelist())
        self.rules.create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.prod_3.id,
            'discount': 10.0,
        })
        order = self.sale_orders.create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': self.prod_3.id, 'product_uom_qty': 1.0})],
        })
        line = order.order_line
        self.assertEqual(self._line_badge(line), ('product', 'Desc. Producto (10.0%)'))

        line.write({'discount': 5.0})
        self.assertEqual(self._line_values(line), (100.0, 0.0, 0.0, 5.0))
        self.assertEqual(self._line_badge(line), ('none', ''))
