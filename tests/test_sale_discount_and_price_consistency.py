# -*- coding: utf-8 -*-

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaleDiscountAndPriceConsistency(TransactionCase):
    """Consistencia de descuentos masivos, actualización de precios y cálculo fiscal."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
            | cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
            | cls.env.ref('entintados_pdv.group_sale_edit_discount')
        )
        cls.company = cls.env.company
        cls.country = cls.company.account_fiscal_country_id or cls.env.ref('base.mx')
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Consistencia Precios',
            'currency_id': cls.company.currency_id.id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Pruebas Consistencia',
            'is_customer': True,
            'phone': '5512345678',
        })
        cls.partner.property_product_pricelist = cls.pricelist

        cls.tax_group = cls.env['account.tax.group'].create({
            'name': 'IVA 16% Consistencia',
            'company_id': cls.company.id,
            'country_id': cls.country.id,
        })
        cls.tax_included_16 = cls.env['account.tax'].create({
            'name': 'IVA 16% Incluido',
            'type_tax_use': 'sale',
            'amount_type': 'percent',
            'amount': 16.0,
            'price_include_override': 'tax_included',
            'company_id': cls.company.id,
            'country_id': cls.country.id,
            'tax_group_id': cls.tax_group.id,
        })

        # Productos estándar
        cls.product_tax_included = cls.env['product.product'].create({
            'name': 'Producto IVA Incluido 116',
            'list_price': 116.0,
            'invoice_policy': 'order',
            'taxes_id': [Command.set(cls.tax_included_16.ids)],
        })
        cls.product_plain = cls.env['product.product'].create({
            'name': 'Brocha Regular',
            'list_price': 50.0,
            'invoice_policy': 'order',
            'taxes_id': [Command.clear()],
        })

        # Configuración entintado
        cls.base_type = cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        cls.size = cls.env['tint.size'].search([('code', '=', 'G')], limit=1)
        cls.point = cls.env.ref('entintados_pdv.uom_tint_point')
        cls.base_product = cls.env['product.product'].create({
            'name': 'Base Pintura 200',
            'default_code': 'TEST-CONS-BASE',
            'tint_role': 'base',
            'tint_base_type_id': cls.base_type.id,
            'tint_size_id': cls.size.id,
            'list_price': 200.0,
            'taxes_id': [Command.clear()],
            'is_storable': True,
            'invoice_policy': 'order',
        })
        cls.colorant_product = cls.env['product.product'].create({
            'name': 'Colorante Rojo 2',
            'default_code': 'TEST-CONS-RED',
            'tint_role': 'colorant',
            'uom_id': cls.point.id,
            'list_price': 2.0,
            'taxes_id': [Command.clear()],
            'is_storable': True,
            'invoice_policy': 'order',
        })
        gallery = cls.env['tint.gallery'].search([], limit=1)
        cls.tint_color = cls.env['tint.color'].create({
            'name': 'Color Prueba Consistencia',
            'gallery_id': gallery.id,
            'code_number': '1122',
        })
        cls.tint_formula = cls.env['tint.color.formula'].create({
            'color_id': cls.tint_color.id,
            'base_type_id': cls.base_type.id,
            'size_id': cls.size.id,
            'line_ids': [Command.create({'colorant_id': cls.colorant_product.id, 'points': 12})],
        })

    def _create_order(self):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
        })

    def test_discount_amount_100_percent_with_tax_included(self):
        """Línea con 100% de descuento e IVA incluido descuenta la base imponible sin impuestos."""
        order = self._create_order()
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product_tax_included.id,
            'product_uom_qty': 1.0,
            'price_unit': 116.0,
            'discount': 100.0,
        })
        self.assertEqual(line.price_subtotal, 0.0)
        # 116 con 16% de IVA incluido tiene base antes de IVA de 100.00.
        # discount_amount debe ser 100.00, no 116.00 (que duplicaba el IVA).
        self.assertEqual(line.discount_amount, 100.0)

    def test_discount_amount_high_percent_rounding(self):
        """Descuento extremo de 99.99% conserva precisión centavo a centavo con compute_all."""
        order = self._create_order()
        product = self.env['product.product'].create({
            'name': 'Producto Fraccionario',
            'list_price': 12.37,
            'invoice_policy': 'order',
            'taxes_id': [Command.set(self.tax_included_16.ids)],
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': product.id,
            'product_uom_qty': 100.0,
            'price_unit': 12.37,
            'discount': 99.99,
        })
        # Verificamos que discount_amount no se desvíe por la división inversa (100 - 99.99)
        base_sin_iva = self.company.currency_id.round(
            self.tax_included_16.compute_all(12.37, quantity=100.0)['total_excluded']
        )
        expected_discount = self.company.currency_id.round(base_sin_iva - line.price_subtotal)
        self.assertEqual(line.discount_amount, expected_discount)

    def test_discount_wizard_all_excluded_lines_raises_user_error(self):
        """Si ninguna línea admite descuento (solo entintado), el wizard lanza UserError."""
        order = self._create_order()
        order._apply_tint(self.tint_formula, self.base_product, 1)

        wizard = self.env['sale.order.discount'].with_context(
            active_model='sale.order',
            active_id=order.id,
        ).create({
            'sale_order_id': order.id,
            'discount_type': 'sol_discount',
            'discount_percentage': 0.10,
        })

        with self.assertRaises(UserError) as cm:
            wizard.action_apply_discount()
        self.assertIn("admite descuento manual", str(cm.exception))

    def test_discount_wizard_mixed_lines_applies_and_logs_warning(self):
        """En orden mixta, descuenta líneas elegibles y registra nota en chatter de las omitidas."""
        order = self._create_order()
        order._apply_tint(self.tint_formula, self.base_product, 1)
        plain_line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product_plain.id,
            'product_uom_qty': 2.0,
            'price_unit': 50.0,
        })

        wizard = self.env['sale.order.discount'].with_context(
            active_model='sale.order',
            active_id=order.id,
        ).create({
            'sale_order_id': order.id,
            'discount_type': 'sol_discount',
            'discount_percentage': 0.10,
        })
        wizard.action_apply_discount()

        # Línea de brocha recibe 10%
        self.assertEqual(plain_line.discount, 10.0)
        self.assertEqual(plain_line.price_subtotal, 90.0)

        # Línea entintada no recibe descuento
        tint_line = order.order_line.filtered(lambda l: l._tint_recipe())
        self.assertTrue(all(l.discount == 0.0 for l in tint_line))

        # El chatter tiene nota informando que se omitieron líneas
        chatter_messages = order.message_ids.mapped('body')
        self.assertTrue(any("descuento manual" in msg or "omit" in msg for msg in chatter_messages))

    def test_action_update_prices_skips_tint_lines_cleanly(self):
        """action_update_prices omite líneas entintadas y actualiza el resto sin error."""
        order = self._create_order()
        order._apply_tint(self.tint_formula, self.base_product, 1)
        plain_line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product_plain.id,
            'product_uom_qty': 1.0,
            'price_unit': 40.0,  # Desactualizado adrede
        })

        # Al actualizar precios, plain_line debe actualizarse a 50.0 (su list_price)
        # y la línea entintada no debe causar ValidationError
        order.action_update_prices()

        self.assertEqual(plain_line.price_unit, 50.0)
        tint_base_line = order.order_line.filtered(lambda l: l.product_id == self.base_product)
        self.assertTrue(tint_base_line)
        self.assertEqual(tint_base_line.price_unit, 200.0)  # Base 200 en sección jerárquica
        colorant_lines = order.order_line.filtered(lambda l: l.tint_section_id == tint_base_line.tint_section_id and l != tint_base_line)
        self.assertEqual(tint_base_line.price_subtotal + sum(colorant_lines.mapped('price_subtotal')), 224.0)
