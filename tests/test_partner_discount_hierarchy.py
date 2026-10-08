# -*- coding: utf-8 -*-

from datetime import timedelta
from unittest.mock import patch

from lxml import etree

from odoo import fields
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
        """En cotización 'sent', guardar un cambio de cliente sin enviar pricelist_id
        persiste la tarifa del nuevo cliente y recalcula con ella."""
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

    # --- Descuento único y origen del precio ---

    def _enable_line_discounts(self):
        self.env.user.group_ids |= self.env.ref('product.group_product_pricelist')
        self.env.user.group_ids |= self.env.ref('sale.group_discount_per_so_line')

    def _create_percentage_pricelist(self, name='Tarifa Veracruz', items=None):
        """Tarifa con reglas `percentage`; por defecto −5 % global."""
        items = items or [{'applied_on': '3_global', 'percent_price': 5.0}]
        return self.env['product.pricelist'].create({
            'name': name,
            'item_ids': [(0, 0, dict(item, compute_price='percentage')) for item in items],
        })

    def _create_pricelist_partner(self, name, pricelist):
        partner = self.partners.create({'name': name, 'is_customer': True, 'discount': 0.0})
        partner.property_product_pricelist = pricelist
        return partner

    def _create_agreement_partner(self, name, pricelist, discount=10.0, product=None):
        partner = self._create_pricelist_partner(name, pricelist)
        self.rules.create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': (product or self.prod_3).id,
            'discount': discount,
        })
        return partner

    def _create_fixed_price_partner(self, name, pricelist, price=70.0, product=None):
        partner = self._create_pricelist_partner(name, pricelist)
        self.rules.create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': (product or self.prod_3).id,
            'fixed_price': price,
        })
        return partner

    def _create_order(self, partner, *products):
        return self.sale_orders.create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1.0}) for product in products],
        })

    def _line_values(self, line):
        """(price_unit, discount, pricing_rule_type, price_origin)."""
        return (round(line.price_unit, 2), round(line.discount, 2), line.pricing_rule_type, line.price_origin)

    def _line_badge(self, line):
        return (line.pricing_rule_type, line.pricing_rule_origin)

    def _apply_discount_wizard(self, order, percentage, discount_type='sol_discount'):
        """Aplica el wizard "Descuento"; `percentage` en fracción (0.05 = 5 %), como el core."""
        wizard = self.env['sale.order.discount'].create({
            'sale_order_id': order.id,
            'discount_type': discount_type,
            'discount_percentage': percentage,
        })
        wizard.action_apply_discount()

    def _edit_first_line(self, order, **values):
        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                for field_name, value in values.items():
                    setattr(line_form, field_name, value)

    def test_pricelist_baked_into_price_unit(self):
        """La tarifa −5 % queda dentro de price_unit, con o sin descuentos por línea activos."""
        self._enable_line_discounts()
        partner = self._create_pricelist_partner('Cliente Veracruz', self._create_percentage_pricelist())
        Item = self.env.registry['product.pricelist.item']
        for discount_feature in (True, False):
            with self.subTest(group_discount_per_so_line=discount_feature), \
                    patch.object(Item, '_is_discount_feature_enabled', lambda self, v=discount_feature: v):
                line = self._create_order(partner, self.prod_3).order_line
                self.assertEqual(self._line_values(line), (95.0, 0.0, 'none', 'pricelist'))
                self.assertAlmostEqual(line.price_subtotal, 95.0)

    def test_agreement_discount_on_pricelist_price(self):
        """El acuerdo 10 % se aplica como único descuento sobre el precio de tarifa."""
        self._enable_line_discounts()
        partner = self._create_agreement_partner('Cliente Veracruz Acuerdo', self._create_percentage_pricelist())
        line = self._create_order(partner, self.prod_3).order_line
        self.assertEqual(self._line_values(line), (95.0, 10.0, 'product', 'pricelist'))
        self.assertEqual(self._line_badge(line), ('product', 'Desc. Producto (10.0%)'))
        self.assertAlmostEqual(line.price_subtotal, 85.50)

    def test_update_prices_keeps_agreement_discount(self):
        """AUD-0021: "Actualizar precios" reaplica el acuerdo vigente sobre el precio de tarifa."""
        self._enable_line_discounts()
        partner = self._create_pricelist_partner(
            'Cliente Tarifa Lista', self.env['product.pricelist'].create({'name': 'Tarifa Lista'}))
        partner_b = self._create_pricelist_partner('Cliente Veracruz B', self._create_percentage_pricelist())
        rule = self.rules.create({
            'partner_id': partner_b.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.prod_3.id,
            'discount': 10.0,
        })
        order = self._create_order(partner, self.prod_3)
        line = order.order_line
        with Form(order) as order_form:
            order_form.partner_id = partner_b

        order.action_update_prices()
        self.assertEqual(self._line_values(line), (95.0, 10.0, 'product', 'pricelist'))
        rule.write({'discount': 15.0})
        order.action_update_prices()
        self.assertEqual(self._line_values(line), (95.0, 15.0, 'product', 'pricelist'))
        self.assertEqual(self._line_badge(line), ('product', 'Desc. Producto (15.0%)'))

    def test_manual_discount_replaces_agreement(self):
        """Un descuento escrito sustituye al del acuerdo, aunque sea menor, y se marca manual."""
        self._enable_line_discounts()
        partner = self._create_agreement_partner('Cliente Manual', self._create_percentage_pricelist())
        expected = (95.0, 7.0, 'manual', 'pricelist')

        with self.subTest('write'):
            line = self._create_order(partner, self.prod_3).order_line
            line.write({'discount': 7.0})
            self.assertEqual(self._line_values(line), expected)
            self.assertEqual(self._line_badge(line), ('manual', 'Desc. Manual (7.0%)'))
            self.assertFalse(line.pricing_rule_id)

        with self.subTest('Form'):
            order = self._create_order(partner, self.prod_3)
            self._edit_first_line(order, discount=7.0)
            self.assertEqual(self._line_values(order.order_line), expected)
            self.assertEqual(self._line_badge(order.order_line), ('manual', 'Desc. Manual (7.0%)'))

    def test_manual_discount_persists(self):
        """Cantidad, "Actualizar precios" y cambio de cliente conservan el descuento manual."""
        self._enable_line_discounts()
        pricelist = self._create_percentage_pricelist()
        partner = self._create_agreement_partner('Cliente Manual Persiste', pricelist)
        partner_b = self._create_agreement_partner('Cliente Manual B', pricelist, discount=15.0)
        expected = (95.0, 7.0, 'manual', 'pricelist')

        def manual_order():
            order = self._create_order(partner, self.prod_3)
            order.order_line.write({'discount': 7.0})
            return order

        with self.subTest('cantidad por write'):
            order = manual_order()
            order.order_line.write({'product_uom_qty': 3.0})
            self.assertEqual(self._line_values(order.order_line), expected)

        with self.subTest('cantidad por Form'):
            order = manual_order()
            self._edit_first_line(order, product_uom_qty=3.0)
            self.assertEqual(self._line_values(order.order_line), expected)

        with self.subTest('Actualizar precios'):
            order = manual_order()
            order.action_update_prices()
            self.assertEqual(self._line_values(order.order_line), expected)

        with self.subTest('cambio de cliente por write'):
            order = manual_order()
            order.write({'partner_id': partner_b.id})
            self.assertEqual(self._line_values(order.order_line), expected)
            self.assertEqual(self._line_badge(order.order_line), ('manual', 'Desc. Manual (7.0%)'))

        with self.subTest('cambio de cliente por Form'):
            order = manual_order()
            with Form(order) as order_form:
                order_form.partner_id = partner_b
            self.assertEqual(self._line_values(order.order_line), expected)

    def test_manual_discount_reset_on_product_change(self):
        """Cambiar el producto de una línea manual reaplica el acuerdo del producto nuevo."""
        self._enable_line_discounts()
        partner = self._create_agreement_partner('Cliente Manual Producto', self._create_percentage_pricelist())
        self.rules.create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.prod_1.id,
            'discount': 12.0,
        })
        expected = (475.0, 12.0, 'product', 'pricelist')

        with self.subTest('write'):
            line = self._create_order(partner, self.prod_3).order_line
            line.write({'discount': 7.0})
            line.write({'product_id': self.prod_1.id})
            self.assertEqual(self._line_values(line), expected)

        with self.subTest('Form'):
            order = self._create_order(partner, self.prod_3)
            order.order_line.write({'discount': 7.0})
            self._edit_first_line(order, product_id=self.prod_1)
            self.assertEqual(self._line_values(order.order_line), expected)

    def test_fixed_price_wins_over_manual(self):
        """Un cliente con precio fijo para el producto gana sobre el descuento y el precio manual."""
        self._enable_line_discounts()
        pricelist = self._create_percentage_pricelist()
        partner = self._create_agreement_partner('Cliente Manual Antes Fijo', pricelist)
        partner_fixed = self._create_fixed_price_partner('Cliente Precio Fijo', pricelist)
        expected = (70.0, 0.0, 'fixed_price', 'fixed_price')

        for label, vals in (('descuento manual', {'discount': 7.0}), ('precio manual', {'price_unit': 90.0})):
            with self.subTest(label):
                order = self._create_order(partner, self.prod_3)
                order.order_line.write(vals)
                order.write({'partner_id': partner_fixed.id})
                self.assertEqual(self._line_values(order.order_line), expected)
                self.assertEqual(self._line_badge(order.order_line), ('fixed_price', 'Precio Fijo'))

    def test_fixed_price_rejects_manual_discount(self):
        """El servidor rechaza descuento o precio distinto en precio fijo y descuento fuera de 0–100."""
        self._enable_line_discounts()
        partner = self._create_fixed_price_partner('Cliente Fijo Restricción', self._create_percentage_pricelist())
        order = self._create_order(partner, self.prod_3, self.prod_1)
        fixed_line = order.order_line.filtered(lambda l: l.product_id == self.prod_3)
        eligible_line = order.order_line.filtered(lambda l: l.product_id == self.prod_1)

        self.assertFalse(fixed_line.manual_discount_allowed)
        self.assertTrue(eligible_line.manual_discount_allowed)
        with self.assertRaises(ValidationError):
            fixed_line.write({'discount': 5.0})
        with self.assertRaises(ValidationError):
            fixed_line.write({'price_unit': 90.0})
        for value in (150.0, -1.0):
            with self.subTest(discount=value), self.assertRaises(ValidationError):
                eligible_line.write({'discount': value})

    def test_manual_price_unit(self):
        """Un precio tecleado se marca manual, conserva el acuerdo y solo se pierde al cambiar producto."""
        self._enable_line_discounts()
        pricelist = self._create_percentage_pricelist()
        partner = self._create_agreement_partner('Cliente Precio Manual', pricelist)
        partner_b = self._create_agreement_partner('Cliente Precio Manual B', pricelist, discount=15.0)

        def manual_order():
            order = self._create_order(partner, self.prod_3)
            order.order_line.write({'price_unit': 90.0})
            return order

        with self.subTest('write'):
            order = manual_order()
            self.assertEqual(self._line_values(order.order_line), (90.0, 10.0, 'product', 'manual'))
            self.assertEqual(order.order_line.price_origin_label, 'Modificado de forma manual')

        with self.subTest('Form'):
            order = self._create_order(partner, self.prod_3)
            self._edit_first_line(order, price_unit=90.0)
            self.assertEqual(self._line_values(order.order_line), (90.0, 10.0, 'product', 'manual'))

        with self.subTest('cantidad'):
            order = manual_order()
            order.order_line.write({'product_uom_qty': 3.0})
            self.assertEqual(self._line_values(order.order_line), (90.0, 10.0, 'product', 'manual'))

        with self.subTest('Actualizar precios'):
            order = manual_order()
            order.action_update_prices()
            self.assertEqual(self._line_values(order.order_line), (90.0, 10.0, 'product', 'manual'))

        with self.subTest('cambio de cliente'):
            order = manual_order()
            order.write({'partner_id': partner_b.id})
            self.assertEqual(self._line_values(order.order_line), (90.0, 15.0, 'product', 'manual'))

        with self.subTest('cambio de producto'):
            order = manual_order()
            order.order_line.write({'product_id': self.prod_1.id})
            self.assertEqual(self._line_values(order.order_line), (475.0, 0.0, 'none', 'pricelist'))

    def test_manual_price_survives_partner_change_in_same_edit(self):
        """AUD-0026: cambiar el cliente y teclear un precio en la misma edición del formulario
        conserva el precio tecleado al guardar, en cualquier orden. Sin él, B tendría 100."""
        self._enable_line_discounts()
        partner = self._create_agreement_partner('Cliente Misma Edición A', self._create_percentage_pricelist())
        partner_b = self._create_agreement_partner(
            'Cliente Misma Edición B', self.env['product.pricelist'].create({'name': 'Tarifa Lista'}),
            discount=15.0)

        def price_values(line):
            return (round(line.price_unit, 2), line.price_origin, line.price_origin_label)

        expected = (90.0, 'manual', 'Modificado de forma manual')

        with self.subTest('cliente y después precio'):
            order = self._create_order(partner, self.prod_3)
            with Form(order) as order_form:
                order_form.partner_id = partner_b
                with order_form.order_line.edit(0) as line_form:
                    line_form.price_unit = 90.0
            self.assertEqual(order.pricelist_id, partner_b.property_product_pricelist)
            self.assertEqual(price_values(order.order_line), expected)

        with self.subTest('precio y después cliente'):
            order = self._create_order(partner, self.prod_3)
            with Form(order) as order_form:
                with order_form.order_line.edit(0) as line_form:
                    line_form.price_unit = 90.0
                order_form.partner_id = partner_b
            self.assertEqual(order.pricelist_id, partner_b.property_product_pricelist)
            self.assertEqual(price_values(order.order_line), expected)

    def _create_same_type_agreement_partner(self, name, rule_type, discount):
        """Cliente con tarifa sin reglas y un acuerdo `rule_type` de `discount` %.

        Devuelve el cliente y el producto al que aplica el acuerdo.
        """
        partner = self._create_pricelist_partner(name, self.env['product.pricelist'].create({'name': 'Tarifa Lista'}))
        if rule_type == 'global':
            partner.discount = discount / 100.0
            return partner, self.prod_3
        target = {
            'product': ('0_product', 'product_id', self.prod_3),
            'line': ('1_line', 'line_id', self.line_1),
            'scheme': ('2_scheme', 'scheme_id', self.schema_a),
        }[rule_type]
        applied_on, field_name, record = target
        self.rules.create({
            'partner_id': partner.id,
            'applied_on': applied_on,
            'rule_type': 'discount',
            field_name: record.id,
            'discount': discount,
        })
        return partner, (self.prod_3 if rule_type == 'product' else self.prod_1)

    def test_partner_change_same_rule_type_keeps_agreement(self):
        """Cambiar en el formulario a un cliente con acuerdo del mismo tipo y otro % guarda el
        acuerdo del cliente nuevo, no un descuento manual."""
        self._enable_line_discounts()
        expected_badges = {
            'product': 'Desc. Producto (15.0%)',
            'line': f"Desc. Línea: {self.line_1.name} (15.0%)",
            'scheme': f"Desc. Esquema: {self.schema_a.name} (15.0%)",
            'global': 'Desc. Global Cliente (15.0%)',
        }
        for rule_type, expected_badge in expected_badges.items():
            with self.subTest(rule_type=rule_type):
                partner, product = self._create_same_type_agreement_partner(
                    f'Cliente {rule_type} 10', rule_type, 10.0)
                partner_b, __ = self._create_same_type_agreement_partner(
                    f'Cliente {rule_type} 15', rule_type, 15.0)
                order = self._create_order(partner, product)
                line = order.order_line
                self.assertEqual((round(line.discount, 2), line.pricing_rule_type), (10.0, rule_type))

                with Form(order) as order_form:
                    order_form.partner_id = partner_b

                self.assertEqual(round(line.discount, 2), 15.0)
                self.assertEqual(self._line_badge(line), (rule_type, expected_badge))

    def test_manual_discount_still_detected(self):
        """Un descuento tecleado, o escrito sin `technical_discount` o con otro distinto, queda manual."""
        self._enable_line_discounts()
        partner = self._create_agreement_partner(
            'Cliente Control Manual', self.env['product.pricelist'].create({'name': 'Tarifa Lista'}))
        expected = ('manual', 'Desc. Manual (7.0%)')

        with self.subTest('formulario'):
            line = self._create_order(partner, self.prod_3).order_line
            self._edit_first_line(line.order_id, discount=7.0)
            self.assertEqual(round(line.discount, 2), 7.0)
            self.assertEqual(self._line_badge(line), expected)

        with self.subTest('write sin technical_discount'):
            line = self._create_order(partner, self.prod_3).order_line
            line.write({'discount': 7.0})
            self.assertEqual(self._line_badge(line), expected)

        with self.subTest('write con technical_discount distinto'):
            line = self._create_order(partner, self.prod_3).order_line
            line.write({'discount': 7.0, 'technical_discount': 10.0})
            self.assertEqual(self._line_badge(line), expected)
            self.assertEqual(round(line.technical_discount, 2), 7.0)

    def test_write_discount_with_technical_discount_not_manual(self):
        """`discount` y `technical_discount` iguales en un `write` no cambian el tipo ni el badge."""
        self._enable_line_discounts()
        partner = self._create_agreement_partner(
            'Cliente Write Técnico', self.env['product.pricelist'].create({'name': 'Tarifa Lista'}))
        line = self._create_order(partner, self.prod_3).order_line
        line.write({'discount': 7.0, 'technical_discount': 7.0})
        self.assertEqual(round(line.discount, 2), 7.0)
        self.assertEqual(self._line_badge(line), ('product', 'Desc. Producto (10.0%)'))

    def test_pricelist_switch_never_marks_price_manual(self):
        """Cambiar de tarifa en el formulario y guardar nunca deja el origen en manual, aunque la
        tarifa dé más decimales que los de `price_unit` (12.25 - 10 % = 11.025 → 11.03)."""
        self.env.user.group_ids |= self.env.ref('product.group_product_pricelist')
        product = self.products.create({'name': 'Producto 12.25', 'list_price': 12.25})

        def percentage(name, percent):
            return self.env['product.pricelist'].create({'name': name, 'item_ids': [(0, 0, {
                'applied_on': '0_product_variant',
                'product_id': product.id,
                'compute_price': 'percentage',
                'percent_price': percent,
            })]})

        pricelist_a = self.env['product.pricelist'].create({'name': 'Tarifa Lista'})
        pricelist_b = percentage('Tarifa -10 %', 10.0)
        pricelist_c = percentage('Tarifa -3.33 %', 3.33)
        partner = self.partners.create({'name': 'Cliente Cambio Tarifa', 'is_customer': True})
        partner.property_product_pricelist = pricelist_a
        order = self.sale_orders.create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1.0})],
        })
        line = order.order_line
        # De C a B el origen no cambia ('pricelist'), así que el formulario no lo envía al guardar.
        for pricelist, price, origin in (
            (pricelist_c, 11.84, 'pricelist'),
            (pricelist_b, 11.03, 'pricelist'),
            (pricelist_a, 12.25, 'list'),
            (pricelist_c, 11.84, 'pricelist'),
            (pricelist_b, 11.03, 'pricelist'),
        ):
            with Form(order) as order_form:
                order_form.pricelist_id = pricelist
            self.assertEqual((line.price_unit, line.price_origin), (price, origin), pricelist.name)

        # Un precio tecleado sigue siendo manual.
        self._edit_first_line(order, price_unit=11.5)
        self.assertEqual((line.price_unit, line.price_origin), (11.5, 'manual'))

    def test_price_origin_list_and_labels(self):
        """El origen del precio distingue precio de venta, tarifa y precio fijo, con su detalle."""
        self._enable_line_discounts()
        pricelist = self._create_percentage_pricelist(items=[{
            'applied_on': '0_product_variant',
            'product_id': self.prod_3.id,
            'percent_price': 5.0,
        }])
        partner = self._create_fixed_price_partner('Cliente Origen', pricelist, product=self.prod_2)
        order = self._create_order(partner, self.prod_1, self.prod_3, self.prod_2)
        list_line, pricelist_line, fixed_line = order.order_line

        self.assertEqual(self._line_values(list_line), (500.0, 0.0, 'none', 'list'))
        self.assertEqual(list_line.price_origin_label, 'Precio de venta del producto')

        self.assertEqual(self._line_values(pricelist_line), (95.0, 0.0, 'none', 'pricelist'))
        self.assertIn(pricelist.name, pricelist_line.price_origin_label)
        self.assertIn(pricelist.item_ids.name, pricelist_line.price_origin_label)

        self.assertEqual(self._line_values(fixed_line), (70.0, 0.0, 'fixed_price', 'fixed_price'))
        self.assertEqual(fixed_line.price_origin_label, fixed_line.pricing_rule_origin)

    def test_wizard_line_discount_replaces(self):
        """El wizard "En todas las líneas" sustituye el descuento y marca manual solo líneas elegibles."""
        self._enable_line_discounts()
        partner = self._create_fixed_price_partner('Cliente Wizard', self._create_percentage_pricelist())
        self.rules.create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.prod_1.id,
            'discount': 10.0,
        })
        order = self._create_order(partner, self.prod_3, self.prod_1)
        fixed_line = order.order_line.filtered(lambda l: l.product_id == self.prod_3)
        eligible_line = order.order_line.filtered(lambda l: l.product_id == self.prod_1)

        self._apply_discount_wizard(order, 0.10, discount_type='so_discount')
        discount_product = order.company_id.sale_discount_product_id
        discount_line = order.order_line.filtered(lambda l: l.product_id == discount_product)
        self.assertTrue(discount_line)

        program = self.env['loyalty.program'].create({
            'name': 'Promo Wizard',
            'program_type': 'promotion',
            'reward_ids': [(0, 0, {'reward_type': 'discount', 'discount': 10.0})],
        })
        reward = program.reward_ids
        reward_line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': reward.discount_line_product_id.id,
            'reward_id': reward.id,
            'name': 'Recompensa',
            'price_unit': -10.0,
        })
        self.assertTrue(reward_line.is_reward_line)
        untouched = {line: self._line_values(line) for line in (discount_line, reward_line)}

        self._apply_discount_wizard(order, 0.05)
        self.assertEqual(self._line_values(eligible_line), (475.0, 5.0, 'manual', 'pricelist'))
        self.assertEqual(self._line_badge(eligible_line), ('manual', 'Desc. Manual (5.0%)'))
        self.assertEqual(self._line_values(fixed_line), (70.0, 0.0, 'fixed_price', 'fixed_price'))
        for line, values in untouched.items():
            self.assertEqual(self._line_values(line), values)

        self._apply_discount_wizard(order, 0.0)
        self.assertEqual(self._line_values(eligible_line), (475.0, 0.0, 'manual', 'pricelist'))
        self.assertEqual(self._line_badge(eligible_line), ('manual', 'Desc. Manual (0.0%)'))

    def test_saved_line_qty_change_follows_pricelist(self):
        """Línea guardada sin manual: subir a un escalón de tarifa actualiza precio y origen."""
        self._enable_line_discounts()
        pricelist = self._create_percentage_pricelist(name='Tarifa Escalón', items=[{
            'applied_on': '0_product_variant',
            'product_id': self.prod_3.id,
            'percent_price': 20.0,
            'min_quantity': 10.0,
        }])
        partner = self._create_pricelist_partner('Cliente Escalón', pricelist)

        for label in ('write', 'Form'):
            with self.subTest(label):
                order = self._create_order(partner, self.prod_3)
                line = order.order_line
                self.assertEqual(self._line_values(line), (100.0, 0.0, 'none', 'list'))
                if label == 'write':
                    line.write({'product_uom_qty': 12.0})
                else:
                    self._edit_first_line(order, product_uom_qty=12.0)
                self.assertEqual(self._line_values(line), (80.0, 0.0, 'none', 'pricelist'))
                self.assertIn(pricelist.name, line.price_origin_label)

    def test_update_prices_with_fixed_price_line(self):
        """"Actualizar precios" con una línea de precio fijo bajo tarifa de porcentaje."""
        self._enable_line_discounts()
        partner = self._create_fixed_price_partner('Cliente Fijo Actualizar', self._create_percentage_pricelist())
        prod_q = self.templates.create({'name': 'Rodillo 9 Pulgadas', 'list_price': 100.0}).product_variant_ids[0]
        order = self._create_order(partner, self.prod_3, prod_q)
        fixed_line = order.order_line.filtered(lambda l: l.product_id == self.prod_3)
        plain_line = order.order_line.filtered(lambda l: l.product_id == prod_q)

        for attempt in (1, 2):
            with self.subTest(attempt=attempt):
                order.action_update_prices()
                self.assertEqual(self._line_values(fixed_line), (70.0, 0.0, 'fixed_price', 'fixed_price'))
                self.assertEqual(self._line_values(plain_line), (95.0, 0.0, 'none', 'pricelist'))

    def test_partner_change_from_fixed_price_line(self):
        """Cambiar el cliente de una cotización con una línea que ya es de precio fijo."""
        self._enable_line_discounts()
        pricelist = self._create_percentage_pricelist()
        partner_fixed = self._create_fixed_price_partner('Cliente Fijo Origen', pricelist)
        targets = (
            ('precio fijo 60', self._create_fixed_price_partner('Cliente Fijo 60', pricelist, price=60.0),
             (60.0, 0.0, 'fixed_price', 'fixed_price')),
            ('sin acuerdo', self._create_pricelist_partner('Cliente Sin Acuerdo', pricelist),
             (95.0, 0.0, 'none', 'pricelist')),
            ('acuerdo 10 %', self._create_agreement_partner('Cliente Acuerdo 10', pricelist),
             (95.0, 10.0, 'product', 'pricelist')),
        )
        for label, target, expected in targets:
            with self.subTest(label):
                order = self._create_order(partner_fixed, self.prod_3)
                self.assertEqual(self._line_values(order.order_line), (70.0, 0.0, 'fixed_price', 'fixed_price'))
                order.write({'partner_id': target.id})
                self.assertEqual(self._line_values(order.order_line), expected)

    def test_update_prices_button_hidden(self):
        """El botón "Actualizar precios" queda oculto en el formulario del pedido."""
        # Con el grupo de tarifas el botón está en la arquitectura (sin él, el core lo quita).
        self._enable_line_discounts()
        arch = self.env['sale.order'].get_views([(False, 'form')])['views']['form']['arch']
        buttons = etree.fromstring(arch).xpath("//button[@name='action_update_prices']")
        self.assertEqual(len(buttons), 1)
        self.assertIn(buttons[0].get('invisible'), ('1', 'True', 'true'))


    # --- Precio fijo y UdM de la línea ---

    def _create_fixed_price_uom_partner(self, name):
        """Producto en Unidades con precio de lista 400 y cliente con precio fijo 300."""
        product = self.templates.create({'name': f'Esmalte {name}', 'list_price': 400.0}).product_variant_ids[0]
        pricelist = self.env['product.pricelist'].create({'name': f'Tarifa {name}'})
        partner = self._create_fixed_price_partner(f'Cliente {name}', pricelist, price=300.0, product=product)
        return partner, product

    def test_fixed_price_new_line_converts_uom(self):
        """Una línea nueva de precio fijo toma el precio convertido a su UdM."""
        self.env.user.group_ids |= self.env.ref('uom.group_uom')
        partner, product = self._create_fixed_price_uom_partner('UdM Nueva')
        pack_6 = self.env.ref('uom.product_uom_pack_6')
        expected = (1800.0, 0.0, 'fixed_price', 'fixed_price')

        with self.subTest('creación directa'):
            order = self.sale_orders.create({
                'partner_id': partner.id,
                'order_line': [(0, 0, {
                    'product_id': product.id,
                    'product_uom_qty': 1.0,
                    'product_uom_id': pack_6.id,
                })],
            })
            self.assertEqual(self._line_values(order.order_line), expected)

        with self.subTest('formulario sin guardar'):
            order_form = Form(self.sale_orders)
            order_form.partner_id = partner
            with order_form.order_line.new() as line_form:
                line_form.product_id = product
                self.assertEqual(line_form.price_unit, 300.0)
                line_form.product_uom_id = pack_6
                self.assertEqual(line_form.price_unit, 1800.0)
            order = order_form.save()
            self.assertEqual(self._line_values(order.order_line), expected)
            self.assertEqual(order.order_line.technical_price_unit, 1800.0)

    def test_fixed_price_saved_line_uom_change(self):
        """En una cotización guardada, cambiar la UdM convierte el precio fijo."""
        self.env.user.group_ids |= self.env.ref('uom.group_uom')
        partner, product = self._create_fixed_price_uom_partner('UdM Guardada')
        order = self._create_order(partner, product)
        line = order.order_line
        self.assertEqual(self._line_values(line), (300.0, 0.0, 'fixed_price', 'fixed_price'))

        steps = (
            ('Pack of 6', self.env.ref('uom.product_uom_pack_6'), 1800.0),
            ('Unidades', product.uom_id, 300.0),
            ('Docenas', self.env.ref('uom.product_uom_dozen'), 3600.0),
        )
        for label, uom, price in steps:
            with self.subTest(label):
                self._edit_first_line(order, product_uom_id=uom)
                self.assertEqual(self._line_values(line), (price, 0.0, 'fixed_price', 'fixed_price'))
                self.assertEqual(line.technical_price_unit, price)

    def test_fixed_price_saved_line_keeps_frozen_on_uom_change(self):
        """Si la regla cambió en la ficha, el cambio de UdM convierte el precio congelado."""
        self.env.user.group_ids |= self.env.ref('uom.group_uom')
        partner, product = self._create_fixed_price_uom_partner('UdM Congelada')
        order = self._create_order(partner, product)
        self.rules.search([('partner_id', '=', partner.id)]).fixed_price = 280.0

        self._edit_first_line(order, product_uom_id=self.env.ref('uom.product_uom_pack_6'))
        self.assertEqual(self._line_values(order.order_line), (1800.0, 0.0, 'fixed_price', 'fixed_price'))

    def test_fixed_price_write_uom(self):
        """`write` por código de la UdM, sola o con el producto, convierte el precio fijo."""
        partner, product = self._create_fixed_price_uom_partner('UdM Write')
        pack_6 = self.env.ref('uom.product_uom_pack_6')
        expected = (1800.0, 0.0, 'fixed_price', 'fixed_price')

        with self.subTest('solo UdM'):
            line = self._create_order(partner, product).order_line
            line.write({'product_uom_id': pack_6.id})
            self.assertEqual(self._line_values(line), expected)
            self.assertEqual(line.technical_price_unit, line.price_unit)

        with self.subTest('producto y UdM'):
            line = self._create_order(partner, self.prod_3).order_line
            line.write({'product_id': product.id, 'product_uom_id': pack_6.id})
            self.assertEqual(self._line_values(line), expected)
            self.assertEqual(line.technical_price_unit, line.price_unit)

    def test_fixed_price_rule_name_shows_uom(self):
        """La regla de precio fijo muestra la UdM del producto en su nombre."""
        partner, product = self._create_fixed_price_uom_partner('UdM Ficha')
        rule = self.rules.search([('partner_id', '=', partner.id)])
        self.assertEqual(rule.product_uom_id, product.uom_id)
        self.assertEqual(
            rule.name,
            f"{product.display_name}: 300.00 {self.env.company.currency_id.name} / {product.uom_id.name}",
        )

    # --- Precio fijo y moneda del pedido ---

    def _setup_other_currency(self, rates=((None, 17.0),)):
        """Moneda O distinta de la de la compañía C, con `1 C = rate O` desde cada fecha.

        Se borran las tasas de C para que valga 1 frente a sí misma. Devuelve (C, O).
        """
        company = self.env.company
        company_currency = company.currency_id
        usd, mxn = self.env.ref('base.USD'), self.env.ref('base.MXN')
        other = mxn if company_currency == usd else usd
        other.active = True
        self.env['res.currency.rate'].search([
            ('currency_id', 'in', (company_currency | other).ids),
            ('company_id', 'in', (company.id, False)),
        ]).unlink()
        for date, rate in rates:
            self.env['res.currency.rate'].create({
                'name': date or fields.Date.today() - timedelta(days=30),
                'currency_id': other.id,
                'company_id': company.id,
                'rate': rate,
            })
        return company_currency, other

    def _create_currency_pricelist(self, name, currency):
        return self.env['product.pricelist'].create({'name': name, 'currency_id': currency.id})

    def _create_fixed_price_currency_partner(self, name, pricelist):
        """Producto en Unidades con precio de lista 100 y cliente con precio fijo 70."""
        product = self.templates.create({'name': f'Sellador {name}', 'list_price': 100.0}).product_variant_ids[0]
        partner = self._create_fixed_price_partner(f'Cliente {name}', pricelist, price=70.0, product=product)
        return partner, product

    def test_fixed_price_new_line_converts_currency(self):
        """Una línea nueva de precio fijo toma el precio convertido a la moneda de la tarifa."""
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C Nueva', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O Nueva', other)

        with self.subTest('tarifa en moneda de la compañía'):
            partner, product = self._create_fixed_price_currency_partner('Moneda C', pricelist_c)
            order = self._create_order(partner, product)
            self.assertEqual(order.currency_id, company_currency)
            self.assertEqual(self._line_values(order.order_line), (70.0, 0.0, 'fixed_price', 'fixed_price'))

        with self.subTest('tarifa en otra moneda'):
            partner, product = self._create_fixed_price_currency_partner('Moneda O', pricelist_o)
            order = self._create_order(partner, product)
            self.assertEqual(order.currency_id, other)
            self.assertEqual(self._line_values(order.order_line), (1190.0, 0.0, 'fixed_price', 'fixed_price'))
            self.assertEqual(order.order_line.technical_price_unit, 1190.0)

        with self.subTest('otra moneda y "Pack of 6"'):
            partner, product = self._create_fixed_price_currency_partner('Moneda O Pack', pricelist_o)
            order = self.sale_orders.create({
                'partner_id': partner.id,
                'order_line': [(0, 0, {
                    'product_id': product.id,
                    'product_uom_qty': 1.0,
                    'product_uom_id': self.env.ref('uom.product_uom_pack_6').id,
                })],
            })
            self.assertEqual(self._line_values(order.order_line), (7140.0, 0.0, 'fixed_price', 'fixed_price'))

    def test_fixed_price_currency_uses_order_date_rate(self):
        """La conversión usa la tasa vigente en `date_order`, no la del día de ejecución."""
        today = fields.Date.today()
        _company_currency, other = self._setup_other_currency(rates=(
            (today - timedelta(days=400), 17.0),
            (today - timedelta(days=10), 20.0),
        ))
        pricelist_o = self._create_currency_pricelist('T-O Fecha', other)
        partner, product = self._create_fixed_price_currency_partner('Moneda Fecha', pricelist_o)
        order = self.sale_orders.create({
            'partner_id': partner.id,
            'date_order': fields.Datetime.now() - timedelta(days=200),
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1.0})],
        })
        self.assertEqual(self._line_values(order.order_line), (1190.0, 0.0, 'fixed_price', 'fixed_price'))

    def test_fixed_price_saved_line_pricelist_currency_change(self):
        """En una cotización guardada, cambiar la tarifa a otra moneda convierte el precio congelado."""
        self._enable_line_discounts()
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C Form', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O Form', other)

        with self.subTest('ida y vuelta'):
            partner, product = self._create_fixed_price_currency_partner('Moneda Form', pricelist_c)
            order = self._create_order(partner, product)
            line = order.order_line
            for label, pricelist, currency, price in (
                ('T-C → T-O', pricelist_o, other, 1190.0),
                ('T-O → T-C', pricelist_c, company_currency, 70.0),
            ):
                with self.subTest(label):
                    with Form(order) as order_form:
                        order_form.pricelist_id = pricelist
                    self.assertEqual(order.currency_id, currency)
                    self.assertEqual(self._line_values(line), (price, 0.0, 'fixed_price', 'fixed_price'))
                    self.assertEqual(line.technical_price_unit, price)

        with self.subTest('regla cambiada en la ficha'):
            partner, product = self._create_fixed_price_currency_partner('Moneda Form Congelada', pricelist_c)
            order = self._create_order(partner, product)
            self.rules.search([('partner_id', '=', partner.id)]).fixed_price = 60.0
            with Form(order) as order_form:
                order_form.pricelist_id = pricelist_o
            self.assertEqual(self._line_values(order.order_line), (1190.0, 0.0, 'fixed_price', 'fixed_price'))

    def test_fixed_price_currency_round_trip_converts_in_form(self):
        """Tras ida y vuelta de moneda guardando, el precio fijo vuelve exacto y el siguiente cambio
        de tarifa lo convierte en el formulario, no hasta guardar.

        Con 1 C = 0.0544401 O, 70 C → 3.81 O; reconvertir el redondeado daría 69.99 C con
        `technical_price_unit` = 70, y la línea parecería de precio manual.
        """
        self._enable_line_discounts()
        company_currency, other = self._setup_other_currency(rates=((None, 0.0544401376),))
        pricelist_c = self._create_currency_pricelist('T-C Redondeo', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O Redondeo', other)
        partner, product = self._create_fixed_price_currency_partner('Moneda Redondeo', pricelist_c)
        order = self._create_order(partner, product)
        line = order.order_line

        for pricelist, price in ((pricelist_o, 3.81), (pricelist_c, 70.0)):
            with Form(order) as order_form:
                order_form.pricelist_id = pricelist
            self.assertEqual(line.price_unit, price, pricelist.name)
            self.assertFalse(line._has_core_manual_price(), pricelist.name)
        self.assertEqual(line.technical_price_unit, 70.0)

        with Form(order) as order_form:
            order_form.pricelist_id = pricelist_o
            with order_form.order_line.edit(0) as line_form:
                # Lo que ve el usuario antes de guardar.
                self.assertEqual(line_form.price_unit, 3.81)
        self.assertEqual(self._line_values(line), (3.81, 0.0, 'fixed_price', 'fixed_price'))

    def test_fixed_price_uom_change_after_currency_change(self):
        """Tras cambiar de moneda, cambiar solo la UdM guarda el precio que mostró el formulario.

        Con 1 C = 0.0544401376 O, 70 C → 3.810809632 O: por docena da 45.73, pero desde el
        redondeado 3.81 daría 45.72, y de vuelta a C y por unidad, 69.99.
        """
        self._enable_line_discounts()
        self.env.user.group_ids |= self.env.ref('uom.group_uom')
        company_currency, other = self._setup_other_currency(rates=((None, 0.0544401376),))
        pricelist_c = self._create_currency_pricelist('T-C UdM Tras Moneda', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O UdM Tras Moneda', other)
        dozen = self.env.ref('uom.product_uom_dozen')

        for path in ('Form', 'write'):
            with self.subTest(path=path):
                partner, product = self._create_fixed_price_currency_partner(
                    f'UdM Tras Moneda {path}', pricelist_c)
                order = self._create_order(partner, product)
                line = order.order_line
                self._save_pricelist_and_line(order, path, {}, pricelist=pricelist_o)
                self.assertEqual(line.price_unit, 3.81)

                if path == 'Form':
                    with Form(order) as order_form:
                        with order_form.order_line.edit(0) as line_form:
                            line_form.product_uom_id = dozen
                            # Lo que ve el usuario antes de guardar.
                            self.assertEqual(round(line_form.price_unit, 2), 45.73)
                else:
                    self._save_pricelist_and_line(order, path, {'product_uom_id': dozen})
                self.assertEqual(self._line_values(line), (45.73, 0.0, 'fixed_price', 'fixed_price'))

                self._save_pricelist_and_line(order, path, {}, pricelist=pricelist_c)
                self._save_pricelist_and_line(order, path, {'product_uom_id': product.uom_id})
                self.assertEqual(self._line_values(line), (70.0, 0.0, 'fixed_price', 'fixed_price'))

    def test_fixed_price_write_pricelist_currency(self):
        """`write` por código de la tarifa convierte el precio fijo congelado."""
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C Write', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O Write', other)
        partner, product = self._create_fixed_price_currency_partner('Moneda Write', pricelist_c)
        order = self._create_order(partner, product)
        line = order.order_line

        order.write({'pricelist_id': pricelist_o.id})
        self.assertEqual(order.currency_id, other)
        self.assertEqual(self._line_values(line), (1190.0, 0.0, 'fixed_price', 'fixed_price'))
        self.assertEqual(line.technical_price_unit, line.price_unit)

    def test_fixed_price_write_product_currency(self):
        """`write` por código del producto aplica el precio fijo convertido a la moneda del pedido."""
        _company_currency, other = self._setup_other_currency()
        pricelist_o = self._create_currency_pricelist('T-O Producto', other)
        partner, product = self._create_fixed_price_currency_partner('Moneda Producto', pricelist_o)
        line = self._create_order(partner, self.prod_3).order_line

        line.write({'product_id': product.id})
        self.assertEqual(self._line_values(line), (1190.0, 0.0, 'fixed_price', 'fixed_price'))
        self.assertEqual(line.technical_price_unit, line.price_unit)

    def test_fixed_price_partner_change_currency(self):
        """Cambiar a un cliente con precio fijo en una cotización en otra moneda lo convierte."""
        _company_currency, other = self._setup_other_currency()
        pricelist_o = self._create_currency_pricelist('T-O Cliente', other)
        partner, product = self._create_fixed_price_currency_partner('Moneda Cliente', pricelist_o)
        plain_partner = self._create_pricelist_partner('Cliente Moneda Sin Acuerdo', pricelist_o)
        order = self._create_order(plain_partner, product)
        self.assertEqual(round(order.order_line.price_unit, 2), 1700.0)

        order.write({'partner_id': partner.id})
        self.assertEqual(order.currency_id, other)
        self.assertEqual(self._line_values(order.order_line), (1190.0, 0.0, 'fixed_price', 'fixed_price'))

    def test_fixed_price_date_change_keeps_price(self):
        """Cambiar solo `date_order` no reconvierte el precio fijo, igual que el core con la tarifa."""
        today = fields.Date.today()
        _company_currency, other = self._setup_other_currency(rates=(
            (today - timedelta(days=400), 17.0),
            (today - timedelta(days=10), 20.0),
        ))
        pricelist_o = self._create_currency_pricelist('T-O Cambio Fecha', other)
        partner, product = self._create_fixed_price_currency_partner('Moneda Cambio Fecha', pricelist_o)
        order = self.sale_orders.create({
            'partner_id': partner.id,
            'date_order': fields.Datetime.now() - timedelta(days=200),
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1.0})],
        })
        before = self._line_values(order.order_line)

        order.write({'date_order': fields.Datetime.now()})
        self.assertEqual(self._line_values(order.order_line), before)

    def test_fixed_price_rule_name_shows_currency(self):
        """La regla de precio fijo muestra el código ISO de la moneda de la compañía."""
        pricelist = self.env['product.pricelist'].create({'name': 'Tarifa Moneda Ficha'})
        partner, product = self._create_fixed_price_currency_partner('Moneda Ficha', pricelist)
        rule = self.rules.search([('partner_id', '=', partner.id)])
        self.assertEqual(
            rule.name,
            f"{product.display_name}: 70.00 {self.env.company.currency_id.name} / {product.uom_id.name}",
        )

    # --- Tarifa y otros cambios de la línea en el mismo guardado ---

    def _save_pricelist_and_line(self, order, path, line_values, pricelist=None, partner=None):
        """Cambia tarifa (o cliente) y campos de la primera línea en un solo guardado.

        `line_values` son valores de `Form` (registros); con `write` se pasan sus ids.
        """
        if path == 'Form':
            with Form(order) as order_form:
                if partner:
                    order_form.partner_id = partner
                if pricelist:
                    order_form.pricelist_id = pricelist
                with order_form.order_line.edit(0) as line_form:
                    for field_name, value in line_values.items():
                        setattr(line_form, field_name, value)
            return
        vals = {}
        if partner:
            vals['partner_id'] = partner.id
        if pricelist:
            vals['pricelist_id'] = pricelist.id
        vals['order_line'] = [(1, order.order_line.id, {
            field_name: value.id if hasattr(value, 'ids') else value
            for field_name, value in line_values.items()
        })]
        order.write(vals)

    def test_fixed_price_pricelist_and_uom_same_save(self):
        """Cambiar la tarifa a otra moneda y la UdM en el mismo guardado convierte las dos cosas."""
        self._enable_line_discounts()
        self.env.user.group_ids |= self.env.ref('uom.group_uom')
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C UdM Mismo', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O UdM Mismo', other)
        pack_6 = self.env.ref('uom.product_uom_pack_6')

        for path in ('Form', 'write'):
            for label, start, target, currency, price in (
                ('T-C → T-O', pricelist_c, pricelist_o, other, 7140.0),
                ('T-O → T-C', pricelist_o, pricelist_c, company_currency, 420.0),
            ):
                with self.subTest(path=path, direction=label):
                    partner, product = self._create_fixed_price_currency_partner(
                        f'UdM Mismo {path} {label}', start)
                    order = self._create_order(partner, product)
                    line = order.order_line
                    if path == 'Form':
                        with Form(order) as order_form:
                            order_form.pricelist_id = target
                            with order_form.order_line.edit(0) as line_form:
                                line_form.product_uom_id = pack_6
                                # Lo que ve el usuario antes de guardar.
                                self.assertEqual(round(line_form.price_unit, 2), price)
                    else:
                        self._save_pricelist_and_line(order, path, {'product_uom_id': pack_6}, pricelist=target)
                    self.assertEqual(order.currency_id, currency)
                    self.assertEqual(line.product_uom_id, pack_6)
                    self.assertEqual(self._line_values(line), (price, 0.0, 'fixed_price', 'fixed_price'))
                    self.assertEqual(round(line.technical_price_unit, 2), price)

    def test_fixed_price_currency_change_combined_regressions(self):
        """Otros cambios junto con el de moneda en el mismo guardado: sin doble conversión."""
        self._enable_line_discounts()
        self.env.user.group_ids |= self.env.ref('uom.group_uom')
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C Combinado', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O Combinado', other)
        pack_6 = self.env.ref('uom.product_uom_pack_6')

        for path in ('Form', 'write'):
            with self.subTest(path=path, case='tarifa + producto'):
                partner, product = self._create_fixed_price_currency_partner(f'Combinado Producto {path}', pricelist_c)
                order = self._create_order(partner, self.prod_3)
                self._save_pricelist_and_line(order, path, {'product_id': product}, pricelist=pricelist_o)
                self.assertEqual(order.currency_id, other)
                self.assertEqual(self._line_values(order.order_line), (1190.0, 0.0, 'fixed_price', 'fixed_price'))
                self.assertEqual(round(order.order_line.technical_price_unit, 2), 1190.0)

            with self.subTest(path=path, case='cliente + UdM'):
                partner, product = self._create_fixed_price_currency_partner(f'Combinado Cliente {path}', pricelist_o)
                plain_partner = self._create_pricelist_partner(f'Combinado Sin Acuerdo {path}', pricelist_c)
                order = self._create_order(plain_partner, product)
                self._save_pricelist_and_line(order, path, {'product_uom_id': pack_6}, partner=partner)
                self.assertEqual(order.currency_id, other)
                self.assertEqual(self._line_values(order.order_line), (7140.0, 0.0, 'fixed_price', 'fixed_price'))
                self.assertEqual(round(order.order_line.technical_price_unit, 2), 7140.0)

            with self.subTest(path=path, case='tarifa + cantidad'):
                partner, product = self._create_fixed_price_currency_partner(f'Combinado Cantidad {path}', pricelist_c)
                order = self._create_order(partner, product)
                self._save_pricelist_and_line(order, path, {'product_uom_qty': 3.0}, pricelist=pricelist_o)
                self.assertEqual(order.currency_id, other)
                self.assertEqual(order.order_line.product_uom_qty, 3.0)
                self.assertEqual(self._line_values(order.order_line), (1190.0, 0.0, 'fixed_price', 'fixed_price'))
                self.assertEqual(round(order.order_line.technical_price_unit, 2), 1190.0)

    def test_manual_price_pricelist_and_uom_same_save(self):
        """Un precio manual con tarifa y UdM en un guardado queda igual que con dos guardados."""
        self._enable_line_discounts()
        self.env.user.group_ids |= self.env.ref('uom.group_uom')
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C Manual UdM', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O Manual UdM', other)
        pack_6 = self.env.ref('uom.product_uom_pack_6')

        def manual_line(name):
            product = self.templates.create({'name': f'Manual UdM {name}', 'list_price': 100.0}).product_variant_ids[0]
            partner = self._create_pricelist_partner(f'Cliente Manual UdM {name}', pricelist_c)
            line = self._create_order(partner, product).order_line
            line.write({'price_unit': 90.0})
            self.assertEqual(line.price_origin, 'manual')
            return line

        def snapshot(line):
            return (
                line.order_id.currency_id,
                line.product_uom_id,
                round(line.price_unit, 2),
                round(line.technical_price_unit, 2),
                line.price_origin,
            )

        for path in ('Form', 'write'):
            with self.subTest(path=path):
                separate = manual_line(f'Separado {path}')
                self._save_pricelist_and_line(separate.order_id, path, {}, pricelist=pricelist_o)
                self._save_pricelist_and_line(separate.order_id, path, {'product_uom_id': pack_6})

                combined = manual_line(f'Junto {path}')
                self._save_pricelist_and_line(
                    combined.order_id, path, {'product_uom_id': pack_6}, pricelist=pricelist_o)

                self.assertEqual(snapshot(combined), snapshot(separate))
                self.assertEqual(snapshot(combined)[0], other)

    # --- Cambio de moneda en todas las líneas del pedido ---

    def _create_mixed_currency_order(self, name, pricelist):
        """Cotización guardada con una línea de cada tipo; producto a 100 en moneda de la compañía.

        Devuelve (pedido, {tipo: línea}) con precio fijo 70, acuerdo 10 %, sin acuerdo,
        precio manual 90 y descuento manual 7 %.
        """
        partner = self._create_pricelist_partner(f'Cliente Mixto {name}', pricelist)
        kinds = ('fijo', 'acuerdo', 'sin_acuerdo', 'precio_manual', 'descuento_manual')
        products = {
            kind: self.templates.create({'name': f'{kind} {name}', 'list_price': 100.0}).product_variant_ids[0]
            for kind in kinds
        }
        self.rules.create({
            'partner_id': partner.id, 'applied_on': '0_product', 'rule_type': 'fixed_price',
            'product_id': products['fijo'].id, 'fixed_price': 70.0,
        })
        self.rules.create({
            'partner_id': partner.id, 'applied_on': '0_product', 'rule_type': 'discount',
            'product_id': products['acuerdo'].id, 'discount': 10.0,
        })
        order = self._create_order(partner, *products.values())
        lines = {kind: order.order_line.filtered(lambda l, p=products[kind]: l.product_id == p) for kind in kinds}
        lines['precio_manual'].write({'price_unit': 90.0})
        lines['descuento_manual'].write({'discount': 7.0})
        return order, lines

    def _assert_mixed_currency_lines(self, lines, rate):
        """Precios de `_create_mixed_currency_order` convertidos con `1 C = rate`."""
        expected = {
            'fijo': (70.0 * rate, 0.0, 'fixed_price', 'fixed_price'),
            'acuerdo': (100.0 * rate, 10.0, 'product', 'list'),
            'sin_acuerdo': (100.0 * rate, 0.0, 'none', 'list'),
            'precio_manual': (90.0 * rate, 0.0, 'none', 'manual'),
            'descuento_manual': (100.0 * rate, 7.0, 'manual', 'list'),
        }
        for kind, values in expected.items():
            with self.subTest(kind):
                self.assertEqual(self._line_values(lines[kind]), values)
        # El precio manual sigue siéndolo: el precio calculado de referencia también se convierte.
        self.assertEqual(round(lines['precio_manual'].technical_price_unit, 2), 100.0 * rate)

    def test_currency_change_converts_all_lines_form(self):
        """Cambiar la tarifa a otra moneda en el formulario convierte todas las líneas, ida y vuelta."""
        self._enable_line_discounts()
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C Mixta Form', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O Mixta Form', other)
        order, lines = self._create_mixed_currency_order('Form', pricelist_c)
        self._assert_mixed_currency_lines(lines, 1.0)

        with Form(order) as order_form:
            order_form.pricelist_id = pricelist_o
            with order_form.order_line.edit(order.order_line.ids.index(lines['precio_manual'].id)) as line_form:
                # Lo que ve el usuario antes de guardar.
                self.assertEqual(round(line_form.price_unit, 2), 1530.0)
        self.assertEqual(order.currency_id, other)
        self._assert_mixed_currency_lines(lines, 17.0)

        with Form(order) as order_form:
            order_form.pricelist_id = pricelist_c
        self.assertEqual(order.currency_id, company_currency)
        self._assert_mixed_currency_lines(lines, 1.0)

    def test_currency_change_converts_all_lines_write(self):
        """`write` por código de la tarifa convierte todas las líneas."""
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C Mixta Write', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O Mixta Write', other)
        order, lines = self._create_mixed_currency_order('Write', pricelist_c)

        order.write({'pricelist_id': pricelist_o.id})
        self.assertEqual(order.currency_id, other)
        self._assert_mixed_currency_lines(lines, 17.0)

    def test_currency_change_converts_manual_price_on_partner_change(self):
        """Cambiar a un cliente con tarifa en otra moneda convierte el precio manual."""
        self._enable_line_discounts()
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C Mixta Cliente', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O Mixta Cliente', other)
        partner_o = self._create_pricelist_partner('Cliente Moneda O', pricelist_o)

        with self.subTest('write'):
            order, lines = self._create_mixed_currency_order('Cliente Write', pricelist_c)
            order.write({'partner_id': partner_o.id})
            self.assertEqual(order.currency_id, other)
            self.assertEqual(self._line_values(lines['precio_manual']), (1530.0, 0.0, 'none', 'manual'))
            self.assertEqual(self._line_values(lines['sin_acuerdo']), (1700.0, 0.0, 'none', 'list'))

        with self.subTest('Form'):
            order, lines = self._create_mixed_currency_order('Cliente Form', pricelist_c)
            with Form(order) as order_form:
                order_form.partner_id = partner_o
            self.assertEqual(order.currency_id, other)
            self.assertEqual(self._line_values(lines['precio_manual']), (1530.0, 0.0, 'none', 'manual'))
            self.assertEqual(self._line_values(lines['sin_acuerdo']), (1700.0, 0.0, 'none', 'list'))

    def test_currency_change_converts_global_discount_line(self):
        """La línea de descuento por monto del asistente "Descuento" también se convierte."""
        self._enable_line_discounts()
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C Desc Global', company_currency)
        pricelist_o = self._create_currency_pricelist('T-O Desc Global', other)
        partner = self._create_pricelist_partner('Cliente Desc Global', pricelist_c)
        # El asistente descuenta el monto con impuestos incluidos.
        self.prod_3.taxes_id = False
        order = self._create_order(partner, self.prod_3)
        self.env['sale.order.discount'].create({
            'sale_order_id': order.id,
            'discount_type': 'amount',
            'discount_amount': 50.0,
        }).action_apply_discount()
        discount_line = order.order_line.filtered(lambda l: l._is_global_discount())
        self.assertEqual(discount_line.price_unit, -50.0)

        for label, pricelist, price in (('T-C → T-O', pricelist_o, -850.0), ('T-O → T-C', pricelist_c, -50.0)):
            with self.subTest(label):
                with Form(order) as order_form:
                    order_form.pricelist_id = pricelist
                self.assertEqual(round(discount_line.price_unit, 2), price)
                self.assertEqual(round(order.amount_untaxed, 2), round(-price, 2))

    def test_currency_change_uses_new_pricelist_rule(self):
        """Al cambiar a una tarifa en otra moneda con regla para el producto, se usa esa regla."""
        self._enable_line_discounts()
        company_currency, other = self._setup_other_currency()
        pricelist_c = self._create_currency_pricelist('T-C Regla', company_currency)
        pricelist_o = self.env['product.pricelist'].create({
            'name': 'T-O Regla',
            'currency_id': other.id,
            'item_ids': [(0, 0, {
                'applied_on': '0_product_variant',
                'product_id': self.prod_3.id,
                'compute_price': 'fixed',
                'fixed_price': 1500.0,
            })],
        })
        partner = self._create_pricelist_partner('Cliente Regla Moneda', pricelist_c)

        for label in ('write', 'Form'):
            with self.subTest(label):
                order = self._create_order(partner, self.prod_3)
                line = order.order_line
                # Regla de la tarifa anterior en caché.
                self.assertFalse(line.pricelist_item_id)
                if label == 'write':
                    order.write({'pricelist_id': pricelist_o.id})
                else:
                    with Form(order) as order_form:
                        order_form.pricelist_id = pricelist_o
                self.assertEqual(self._line_values(line), (1500.0, 0.0, 'none', 'pricelist'))
