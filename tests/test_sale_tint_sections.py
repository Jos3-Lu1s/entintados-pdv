from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaleTintSections(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Pruebas Secciones',
            'is_customer': True,
            'phone': '5551234567',
        })
        cls.base_type = cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        cls.size = cls.env['tint.size'].search([('code', '=', 'G')], limit=1)
        cls.point = cls.env.ref('entintados_pdv.uom_tint_point')

        cls.base = cls.env['product.product'].create({
            'name': 'Vinílica Satin Base Media',
            'default_code': 'BASE-SEC-01',
            'tint_role': 'base',
            'tint_base_type_id': cls.base_type.id,
            'tint_size_id': cls.size.id,
            'list_price': 500.0,
            'taxes_id': [Command.clear()],
            'is_storable': True,
            'invoice_policy': 'order',
        })
        cls.colorant_red = cls.env['product.product'].create({
            'name': 'Colorante Rojo',
            'default_code': 'COL-RED-01',
            'tint_role': 'colorant',
            'uom_id': cls.point.id,
            'list_price': 2.0,
            'taxes_id': [Command.clear()],
            'is_storable': True,
            'invoice_policy': 'order',
        })
        cls.colorant_yellow = cls.env['product.product'].create({
            'name': 'Colorante Amarillo',
            'default_code': 'COL-YEL-01',
            'tint_role': 'colorant',
            'uom_id': cls.point.id,
            'list_price': 1.5,
            'taxes_id': [Command.clear()],
            'is_storable': True,
            'invoice_policy': 'order',
        })
        gallery = cls.env['tint.gallery'].search([], limit=1)
        cls.color = cls.env['tint.color'].create({
            'name': 'Naranja Atardecer',
            'gallery_id': gallery.id,
            'code_number': '7777',
        })
        cls.formula = cls.env['tint.color.formula'].create({
            'color_id': cls.color.id,
            'base_type_id': cls.base_type.id,
            'size_id': cls.size.id,
            'line_ids': [
                Command.create({'colorant_id': cls.colorant_red.id, 'points': 20}),
                Command.create({'colorant_id': cls.colorant_yellow.id, 'points': 10}),
            ],
        })
        warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        location = warehouse.lot_stock_id
        cls.env['stock.quant']._update_available_quantity(cls.base, location, 100)
        cls.env['stock.quant']._update_available_quantity(cls.colorant_red, location, 1000)
        cls.env['stock.quant']._update_available_quantity(cls.colorant_yellow, location, 1000)

    def _order(self):
        return self.env['sale.order'].create({'partner_id': self.partner.id})

    def test_section_hierarchy_creation(self):
        """Al aplicar un entintado se genera una sección nativa (line_section) y sus líneas hijas vinculadas."""
        order = self._order()
        lines = order._apply_tint(self.formula, self.base, 2)

        # Debe generar 4 líneas: 1 sección, 1 base, 2 colorantes
        self.assertEqual(len(order.order_line), 4)

        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')
        self.assertEqual(len(section), 1)
        expected_title = '%s + [%s] %s' % (self.base.display_name, self.color.code, self.color.name)
        self.assertEqual(section.name, expected_title)
        self.assertFalse(section.collapse_prices)
        self.assertTrue(section.collapse_composition)  # Por defecto no mostrar composición en la cotización
        self.assertTrue(section.tint_snapshot)
        self.assertEqual(section.tint_container_qty, 2)

        # Líneas hijas
        children = order.order_line - section
        self.assertEqual(len(children), 3)
        for child in children:
            self.assertEqual(child.tint_section_id, section)
            self.assertEqual(child.parent_id, section)

        base_line = children.filtered(lambda l: l.product_id == self.base)
        self.assertEqual(len(base_line), 1)
        self.assertEqual(base_line.name, self.base.display_name)
        self.assertNotIn(self.color.name, base_line.name)
        self.assertFalse(base_line.tint_color_description)
        self.assertFalse(base_line.tint_display_name)
        self.assertEqual(base_line.product_uom_qty, 2)
        self.assertEqual(base_line.price_unit, 500.0)
        self.assertEqual(base_line.price_subtotal, 1000.0)

        red_line = children.filtered(lambda l: l.product_id == self.colorant_red)
        self.assertEqual(len(red_line), 1)
        self.assertEqual(red_line.product_uom_qty, 40)  # 20 puntos * 2 envases
        self.assertEqual(red_line.price_unit, 2.0)
        self.assertEqual(red_line.price_subtotal, 80.0)

        yellow_line = children.filtered(lambda l: l.product_id == self.colorant_yellow)
        self.assertEqual(len(yellow_line), 1)
        self.assertEqual(yellow_line.product_uom_qty, 20)  # 10 puntos * 2 envases
        self.assertEqual(yellow_line.price_unit, 1.5)
        self.assertEqual(yellow_line.price_subtotal, 30.0)

    def test_section_totals_match_subtotals(self):
        """La sección suma exactamente los subtotales de las líneas hijas."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 2)

        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')
        # Total esperado: 1000 (base) + 80 (rojo) + 30 (amarillo) = 1110.0
        section_total = section._get_section_totals('price_subtotal')
        self.assertEqual(section_total, 1110.0)
        self.assertEqual(order.amount_untaxed, 1110.0)

    def test_collapse_prices_and_composition_switches(self):
        """Los campos collapse_prices y collapse_composition se pueden alternar libremente en la sección."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)

        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')
        # Por defecto la composición viene oculta
        self.assertTrue(section.collapse_composition)
        self.assertFalse(section.collapse_prices)

        # Alternar composición a visible
        section.write({'collapse_composition': False})
        self.assertFalse(section.collapse_composition)

        # Alternar ambos
        section.write({'collapse_prices': True, 'collapse_composition': True})
        self.assertTrue(section.collapse_prices)
        self.assertTrue(section.collapse_composition)

    def test_protection_against_manual_line_tampering(self):
        """No se puede editar manualmente cantidad, precio ni producto de las líneas de receta."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)

        base_line = order.order_line.filtered(lambda l: l.product_id == self.base)
        red_line = order.order_line.filtered(lambda l: l.product_id == self.colorant_red)

        with self.assertRaises(ValidationError):
            base_line.product_uom_qty = 5

        with self.assertRaises(ValidationError):
            base_line.price_unit = 999.0

        with self.assertRaises(ValidationError):
            red_line.product_uom_qty = 99

        with self.assertRaises(ValidationError):
            red_line.price_unit = 0.0

    def test_cascade_delete_on_section(self):
        """Al borrar la sección de entintado, se eliminan todas sus líneas hijas en cascada."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        self.assertEqual(len(order.order_line), 4)

        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')
        section.unlink()

        self.assertFalse(order.order_line.exists())
        self.assertEqual(order.amount_untaxed, 0.0)

    def test_cascade_delete_on_base_line(self):
        """Al borrar la línea base de un entintado, se elimina la sección y todos los colorantes."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        self.assertEqual(len(order.order_line), 4)

        base_line = order.order_line.filtered(lambda l: l.product_id == self.base)
        base_line.unlink()

        self.assertFalse(order.order_line.exists())
        self.assertEqual(order.amount_untaxed, 0.0)

    def test_consolidated_invoicing_no_loose_colorants(self):
        """Al facturar, se genera una sola línea de factura (la base con el total consolidado), sin colorantes."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 2)
        order.action_confirm()

        # Las líneas de colorantes deben tener qty_to_invoice = 0
        colorant_lines = order.order_line.filtered(lambda l: l.product_id in (self.colorant_red, self.colorant_yellow))
        for line in colorant_lines:
            self.assertEqual(line.qty_to_invoice, 0)

        # Generar factura
        invoice = order._create_invoices()
        self.assertEqual(len(invoice), 1)

        # La factura debe contener exactamente 1 línea para el producto base
        invoice_lines = invoice.invoice_line_ids.filtered(lambda l: l.display_type in (False, 'product'))
        self.assertEqual(len(invoice_lines), 1)

        inv_line = invoice_lines[0]
        self.assertEqual(inv_line.product_id, self.base)
        self.assertEqual(inv_line.quantity, 2)
        # Precio unitario consolidado: 500 + 40 (rojo: 20*2) + 15 (amarillo: 10*1.5) = 555.0 por envase
        self.assertEqual(inv_line.price_unit, 555.0)
        self.assertEqual(inv_line.price_subtotal, 1110.0)
        self.assertIn(self.color.name, inv_line.name)
        self.assertEqual(invoice.amount_untaxed, 1110.0)

    def test_stock_delivery_native_moves(self):
        """Al confirmar la venta, se generan movimientos de entrega para la base y los colorantes."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 2)
        order.action_confirm()

        self.assertEqual(len(order.picking_ids), 1)
        picking = order.picking_ids

        base_move = picking.move_ids.filtered(lambda m: m.product_id == self.base)
        self.assertEqual(len(base_move), 1)
        self.assertEqual(base_move.product_uom_qty, 2)

        red_move = picking.move_ids.filtered(lambda m: m.product_id == self.colorant_red)
        self.assertEqual(len(red_move), 1)
        self.assertEqual(red_move.product_uom_qty, 40)

        yellow_move = picking.move_ids.filtered(lambda m: m.product_id == self.colorant_yellow)
        self.assertEqual(len(yellow_move), 1)
        self.assertEqual(yellow_move.product_uom_qty, 20)

    def test_pos_quotation_import_consolidated(self):
        """Al consultar la orden desde el PDV, los colorantes se omiten y la base reporta el precio total."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)

        pos_config = self.env['pos.config'].search([], limit=1)
        data = order.load_sale_order_from_pos(pos_config.id)

        line_records = data.get('sale.order.line', [])
        # Debe excluir colorantes
        product_ids = [l.get('product_id') for l in line_records if l.get('product_id')]
        self.assertNotIn(self.colorant_red.id, product_ids)
        self.assertNotIn(self.colorant_yellow.id, product_ids)

        # La base debe reportar precio consolidado (555.0) y receta
        base_data = next(l for l in line_records if l.get('product_id') == self.base.id)
        self.assertEqual(base_data['price_unit'], 555.0)
        self.assertTrue(base_data.get('tint_snapshot'))
        self.assertIn(self.color.name, base_data['name'])

    def test_wizard_applies_section_hierarchy(self):
        """El asistente sale.tint.wizard genera la sección y sus líneas hijas."""
        order = self._order()
        wizard = self.env['sale.tint.wizard'].with_context(default_order_id=order.id).create({
            'order_id': order.id,
            'color_id': self.color.id,
            'formula_id': self.formula.id,
            'base_id': self.base.id,
            'quantity': 2,
        })
        wizard.action_apply()
        self.assertEqual(len(order.order_line), 4)
        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')
        self.assertEqual(len(section), 1)
        self.assertEqual(section.tint_container_qty, 2)
        base_line = order.order_line.filtered(lambda l: l.product_id == self.base)
        self.assertEqual(base_line.product_uom_qty, 2)
