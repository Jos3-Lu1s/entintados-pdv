# -*- coding: utf-8 -*-

from odoo.exceptions import ValidationError
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

    def test_loyalty_wizard_fixed_price_protection(self):
        """Verificar que el wizard de lealtad no altere el precio fijo pactado."""
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
        self.assertEqual(line1.price_unit, 300.0)

        wizard = self.env['sale.loyalty.reward.wizard'].with_context(active_id=order.id).create({
            'loyalty_action_type': 'discount',
        })
        wizard.action_apply_custom()
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

