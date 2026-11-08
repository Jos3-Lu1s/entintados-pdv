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

        session = self.env['pos.session'].search([('state', '=', 'opened')], limit=1)
        if session:
            pos_config = session.config_id
        else:
            pos_config = self.env['pos.config'].search([], limit=1) or self.env['pos.config'].create({'name': 'Test POS Config'})
            session = self.env['pos.session'].create({'config_id': pos_config.id, 'user_id': self.env.uid})
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

    def test_protection_against_manual_sequence_modification(self):
        """Se impide modificar directamente la secuencia de las líneas hijas de un entintado."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)

        base_line = order.order_line.filtered(lambda l: l.product_id == self.base)
        red_line = order.order_line.filtered(lambda l: l.product_id == self.colorant_red)

        with self.assertRaises(ValidationError):
            base_line.write({'sequence': 999})

        with self.assertRaises(ValidationError):
            red_line.write({'sequence': 999})

    def test_reorder_section_header_cascades_children(self):
        """Al mover el encabezado de sección, las líneas hijas se reubican en bloque con secuencias consecutivas."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)

        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')
        base_line = order.order_line.filtered(lambda l: l.product_id == self.base)
        red_line = order.order_line.filtered(lambda l: l.product_id == self.colorant_red)
        yellow_line = order.order_line.filtered(lambda l: l.product_id == self.colorant_yellow)

        section.write({'sequence': 50})
        self.assertEqual(section.sequence, 50)
        self.assertEqual(base_line.sequence, 51)
        self.assertEqual(red_line.sequence, 52)
        self.assertEqual(yellow_line.sequence, 53)

        # La paternidad visual debe mantenerse intacta
        order.invalidate_recordset()
        self.assertEqual(base_line.parent_id, section)
        self.assertEqual(red_line.parent_id, section)
        self.assertEqual(yellow_line.parent_id, section)

    def test_prevent_inserting_foreign_line_in_tint_section(self):
        """Se impide crear productos ordinarios con secuencias comprendidas en el bloque del entintado."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')

        tool_product = self.env['product.product'].create({
            'name': 'Brocha para pintar',
            'list_price': 100.0,
            'is_storable': True,
        })

        # Intercalar entre encabezado y componentes
        with self.assertRaises(ValidationError):
            self.env['sale.order.line'].create({
                'order_id': order.id,
                'product_id': tool_product.id,
                'product_uom_qty': 1,
                'sequence': section.sequence + 1,
            })

        # Misma secuencia que el encabezado
        with self.assertRaises(ValidationError):
            self.env['sale.order.line'].create({
                'order_id': order.id,
                'product_id': tool_product.id,
                'product_uom_qty': 1,
                'sequence': section.sequence,
            })

    def test_prevent_reordering_foreign_line_into_tint_section(self):
        """Se impide arrastrar o reordenar un producto ordinario dentro del rango de secuencia del entintado."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')

        tool_product = self.env['product.product'].create({
            'name': 'Rodillo Felpa',
            'list_price': 150.0,
            'is_storable': True,
        })

        tool_line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': tool_product.id,
            'product_uom_qty': 1,
            'sequence': section.sequence + 50,
        })

        with self.assertRaises(ValidationError):
            tool_line.write({'sequence': section.sequence + 1})

    def test_foreign_product_parent_id_and_report_visibility(self):
        """Líneas ordinarias ubicadas fuera del entintado no toman su parent_id y permanecen visibles en reportes."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')

        tool_product = self.env['product.product'].create({
            'name': 'Cinta de Enmascarar',
            'list_price': 80.0,
            'taxes_id': [Command.clear()],
            'is_storable': True,
        })

        tool_line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': tool_product.id,
            'product_uom_qty': 2,
            'price_unit': 80.0,
            'sequence': section.sequence + 50,
        })
        order.invalidate_recordset()

        # No debe pertenecer a la sección de entintado
        self.assertFalse(tool_line.parent_id)

        # En el reporte a imprimir debe figurar la sección (con composición oculta) y la cinta visible
        report_lines = order._get_order_lines_to_report()
        self.assertIn(section, report_lines)
        self.assertIn(tool_line, report_lines)
        base_line = order.order_line.filtered(lambda l: l.product_id == self.base)
        self.assertNotIn(base_line, report_lines)  # Por collapse_composition = True

        # Los totales deben ser consistentes
        section_total = section._get_section_totals('price_subtotal')
        self.assertEqual(section_total, 555.0)
        self.assertEqual(order.amount_untaxed, 555.0 + 160.0)

    def test_add_foreign_product_without_sequence_to_tinted_order(self):
        """Al agregar un producto ordinario sin secuencia a una orden con entintado, se ubica al final sin colisionar."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')
        tint_children = order.order_line.filtered(lambda l: l.tint_section_id == section)
        max_tint_seq = max(tint_children.mapped('sequence'))

        tool = self.env['product.product'].create({
            'name': 'Espátula Metálica',
            'list_price': 50.0,
            'taxes_id': [Command.clear()],
            'is_storable': True,
        })

        order.write({
            'order_line': [
                Command.create({'product_id': tool.id, 'product_uom_qty': 2})
            ]
        })

        tool_line = order.order_line.filtered(lambda l: l.product_id == tool)
        self.assertEqual(len(tool_line), 1)
        self.assertGreater(tool_line.sequence, max_tint_seq)
        self.assertFalse(tool_line.parent_id)

        report_lines = order._get_order_lines_to_report()
        self.assertIn(section, report_lines)
        self.assertIn(tool_line, report_lines)
        self.assertEqual(order.amount_untaxed, 555.0 + 100.0)

    def test_rpc_spoofed_context_bypass_prevented(self):
        """Llamadas RPC con _tint_sync_sequence externo no pueden romper la cascada secuencial."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        section = order.order_line.filtered(lambda l: l.display_type == 'line_section')
        children = order.order_line.filtered(lambda l: l.tint_section_id == section)

        # Simular intento RPC externo enviando un valor booleano
        section.with_context(_tint_sync_sequence=True).write({'sequence': 80})

        self.assertEqual(section.sequence, 80)
        sorted_children = children.sorted(lambda l: (0 if l.product_id.tint_role != 'colorant' else 1, l.sequence, l.id))
        self.assertEqual(sorted_children[0].sequence, 81)
        self.assertEqual(sorted_children[1].sequence, 82)
        self.assertEqual(sorted_children[2].sequence, 83)

    def test_reorder_loose_products_preserves_tint_integrity(self):
        """Reordenar productos sueltos mediante order.write no afecta ni bloquea las líneas de entintado."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)

        tool1 = self.env['product.product'].create({
            'name': 'Brocha A',
            'list_price': 100.0,
            'is_storable': True,
        })
        tool2 = self.env['product.product'].create({
            'name': 'Brocha B',
            'list_price': 150.0,
            'is_storable': True,
        })

        order.write({
            'order_line': [
                Command.create({'product_id': tool1.id, 'product_uom_qty': 1}),
                Command.create({'product_id': tool2.id, 'product_uom_qty': 1}),
            ]
        })

        sec = order.order_line.filtered(lambda l: l.display_type == 'line_section' and l.tint_snapshot)
        base_l = order.order_line.filtered(lambda l: l.product_id == self.base)
        col_red = order.order_line.filtered(lambda l: l.product_id == self.colorant_red)
        col_yel = order.order_line.filtered(lambda l: l.product_id == self.colorant_yellow)
        t1 = order.order_line.filtered(lambda l: l.product_id == tool1)
        t2 = order.order_line.filtered(lambda l: l.product_id == tool2)

        # Reordenar productos sueltos (t2 antes de t1) simulando resecuenciación en lote de la grilla
        order.write({
            'order_line': [
                Command.update(sec.id, {'sequence': 10}),
                Command.update(base_l.id, {'sequence': 11}),
                Command.update(col_red.id, {'sequence': 12}),
                Command.update(col_yel.id, {'sequence': 13}),
                Command.update(t2.id, {'sequence': 14}),
                Command.update(t1.id, {'sequence': 15}),
            ]
        })

        self.assertEqual(t2.sequence, 14)
        self.assertEqual(t1.sequence, 15)

        # Intentar intercalar un producto suelto dentro del bloque de entintado debe fallar
        with self.assertRaises(ValidationError):
            order.write({
                'order_line': [
                    Command.update(t2.id, {'sequence': 11}),
                ]
            })

    def test_is_tint_section_flag_identification(self):
        """Identificar correctamente el encabezado de entintado mediante is_tint_section."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)

        sec = order.order_line.filtered(lambda l: l.display_type == 'line_section' and l.tint_snapshot)
        children = order.order_line.filtered(lambda l: l.tint_section_id == sec)

        self.assertTrue(sec.is_tint_section)
        for child in children:
            self.assertFalse(child.is_tint_section)

        # Crear una sección regular y una partida ordinaria
        regular_sec = self.env['sale.order.line'].create({
            'order_id': order.id,
            'name': 'Sección Regular',
            'display_type': 'line_section',
        })
        regular_prod = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.base.id,
            'product_uom_qty': 1,
        })
        self.assertFalse(regular_sec.is_tint_section)
        self.assertFalse(regular_prod.is_tint_section)

    def test_swap_tint_section_with_regular_product(self):
        """Intercambiar la posición de una sección de entintado completa con una partida ordinaria."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)

        tool = self.env['product.product'].create({
            'name': 'Rodillo Profesional',
            'list_price': 120.0,
            'is_storable': True,
        })
        tool_line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': tool.id,
            'product_uom_qty': 1,
        })

        sec = order.order_line.filtered(lambda l: l.display_type == 'line_section' and l.tint_snapshot)
        base_line = order.order_line.filtered(lambda l: l.product_id == self.base and l.tint_section_id == sec)
        colorant_lines = order.order_line.filtered(lambda l: l.tint_section_id == sec and l.product_id != self.base)
        tint_block = sec | base_line | colorant_lines

        # Inicialmente el entintado está antes de la herramienta
        self.assertLess(sec.sequence, tool_line.sequence)

        # Mover entintado hacia abajo (intercambiar bloque con tool_line)
        # tool_line toma sequence = 10, y el bloque de entintado toma 11, 12, 13, 14
        order.write({
            'order_line': [
                Command.update(tool_line.id, {'sequence': 10}),
                Command.update(sec.id, {'sequence': 11}),
                Command.update(base_line.id, {'sequence': 12}),
            ] + [Command.update(c.id, {'sequence': 13 + idx}) for idx, c in enumerate(colorant_lines)]
        })

        self.assertEqual(tool_line.sequence, 10)
        self.assertEqual(sec.sequence, 11)
        self.assertEqual(base_line.sequence, 12)
        for idx, c in enumerate(colorant_lines):
            self.assertEqual(c.sequence, 13 + idx)

        # Mover entintado hacia arriba de nuevo (bloque de entintado primero)
        order.write({
            'order_line': [
                Command.update(sec.id, {'sequence': 10}),
                Command.update(base_line.id, {'sequence': 11}),
            ] + [Command.update(c.id, {'sequence': 12 + idx}) for idx, c in enumerate(colorant_lines)]
            + [Command.update(tool_line.id, {'sequence': 12 + len(colorant_lines)})]
        })

        self.assertEqual(sec.sequence, 10)
        self.assertEqual(base_line.sequence, 11)
        self.assertEqual(tool_line.sequence, 12 + len(colorant_lines))

    def _tint_lines(self, order):
        header = order.order_line.filtered('is_tint_section')
        base_line = order.order_line.filtered(lambda l: l.product_id == self.base)
        red_line = order.order_line.filtered(lambda l: l.product_id == self.colorant_red)
        yellow_line = order.order_line.filtered(lambda l: l.product_id == self.colorant_yellow)
        return header, base_line, red_line, yellow_line

    def test_header_containers_rescale_components(self):
        """Cambiar los envases del encabezado escala la base y los colorantes con sus precios congelados."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        header, base_line, red_line, yellow_line = self._tint_lines(order)
        prices = {line: line.price_unit for line in base_line | red_line | yellow_line}
        total_before = order.amount_untaxed

        header.tint_container_qty = 3

        self.assertEqual(base_line.product_uom_qty, 3)
        self.assertEqual(red_line.product_uom_qty, 60)
        self.assertEqual(yellow_line.product_uom_qty, 30)
        for line, price in prices.items():
            self.assertEqual(line.price_unit, price)
            self.assertEqual(line.tint_container_qty, 3)
        self.assertAlmostEqual(order.amount_untaxed, total_before * 3, places=2)

    def test_header_containers_from_order_form_save(self):
        """Guardar la orden con el encabezado y sus componentes ya escalados se acepta en cualquier orden."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        header, base_line, red_line, yellow_line = self._tint_lines(order)

        order.write({'order_line': [
            Command.update(red_line.id, {'product_uom_qty': 40, 'tint_container_qty': 2}),
            Command.update(base_line.id, {'product_uom_qty': 2, 'tint_container_qty': 2}),
            Command.update(yellow_line.id, {'product_uom_qty': 20, 'tint_container_qty': 2}),
            Command.update(header.id, {'tint_container_qty': 2}),
        ]})

        self.assertEqual(header.tint_container_qty, 2)
        self.assertEqual(base_line.product_uom_qty, 2)
        self.assertEqual(red_line.product_uom_qty, 40)
        self.assertEqual(yellow_line.product_uom_qty, 20)

    def test_header_containers_reject_invalid_values(self):
        """Solo se aceptan números enteros de envases mayores que cero."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        header, base_line, _red, _yellow = self._tint_lines(order)

        for value in (0, -1, 1.5):
            with self.assertRaises(ValidationError):
                header.tint_container_qty = value
        self.assertEqual(header.tint_container_qty, 1)
        self.assertEqual(base_line.product_uom_qty, 1)

    def test_header_containers_locked_outside_quotation(self):
        """Una orden confirmada o bloqueada no permite cambiar los envases."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        header, _base, _red, _yellow = self._tint_lines(order)
        order.action_confirm()

        with self.assertRaises(ValidationError):
            header.tint_container_qty = 2

        locked_order = self._order()
        locked_order._apply_tint(self.formula, self.base, 1)
        locked_header = self._tint_lines(locked_order)[0]
        locked_order.locked = True
        with self.assertRaises(ValidationError):
            locked_header.tint_container_qty = 2

    def test_component_quantity_must_match_header(self):
        """Una cantidad de componente que no corresponde a los envases del encabezado se rechaza."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        header, base_line, red_line, _yellow = self._tint_lines(order)

        with self.assertRaises(ValidationError):
            red_line.write({'product_uom_qty': 25})
        with self.assertRaises(ValidationError):
            order.write({'order_line': [
                Command.update(header.id, {'tint_container_qty': 2}),
                Command.update(red_line.id, {'product_uom_qty': 25}),
            ]})
        self.assertEqual(base_line.product_uom_qty, 1)
        self.assertEqual(red_line.product_uom_qty, 20)

    def test_component_price_and_discount_stay_locked(self):
        """Precio y descuento de los componentes siguen bloqueados aunque la cantidad sea editable."""
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        _header, base_line, red_line, _yellow = self._tint_lines(order)

        with self.assertRaises(ValidationError):
            base_line.write({'product_uom_qty': 1, 'price_unit': 1.0})
        with self.assertRaises(ValidationError):
            red_line.write({'discount': 10})
        with self.assertRaises(ValidationError):
            base_line.write({'tint_snapshot': False})
