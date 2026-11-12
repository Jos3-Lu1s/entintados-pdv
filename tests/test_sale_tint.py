from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestSaleTint(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente fórmulas', 'is_customer': True, 'phone': '5555555555'})
        cls.base_type = cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        cls.size = cls.env['tint.size'].search([('code', '=', 'G')], limit=1)
        cls.point = cls.env.ref('entintados_pdv.uom_tint_point')
        cls.base = cls.env['product.product'].create({
            'name': 'Base prueba ventas', 'default_code': 'TEST-TINT-BASE',
            'tint_role': 'base', 'tint_base_type_id': cls.base_type.id,
            'tint_size_id': cls.size.id, 'list_price': 200, 'taxes_id': [Command.clear()],
            'is_storable': True, 'invoice_policy': 'order',
        })
        cls.colorant = cls.env['product.product'].create({
            'name': 'Rojo prueba ventas', 'default_code': 'TEST-TINT-RED',
            'tint_role': 'colorant', 'uom_id': cls.point.id, 'list_price': 2,
            'taxes_id': [Command.clear()], 'is_storable': True, 'invoice_policy': 'order',
        })
        gallery = cls.env['tint.gallery'].search([], limit=1)
        cls.color = cls.env['tint.color'].create({'name': 'Rojo Sangre prueba', 'gallery_id': gallery.id, 'code_number': '9876'})
        cls.formula = cls.env['tint.color.formula'].create({
            'color_id': cls.color.id, 'base_type_id': cls.base_type.id, 'size_id': cls.size.id,
            'line_ids': [Command.create({'colorant_id': cls.colorant.id, 'points': 12})],
        })

    def _order(self):
        return self.env['sale.order'].create({'partner_id': self.partner.id})

    def _stock_lookup(self, order, **values):
        return self.env['product.stock.lookup'].create({
            'sale_order_id': order.id, 'product_id': self.base.id,
            'company_id': order.company_id.id, **values})

    def test_stock_lookup_selects_each_tint_even_with_same_base(self):
        order = self._order()
        first = order._apply_tint(self.formula, self.base, 1)
        color = self.env['tint.color'].create({'name': 'Azul consulta sucursal',
            'gallery_id': self.color.gallery_id.id, 'code_number': '9875'})
        formula = self.env['tint.color.formula'].create({
            'color_id': color.id, 'base_type_id': self.base_type.id, 'size_id': self.size.id,
            'line_ids': [Command.create({'colorant_id': self.colorant.id, 'points': 8})],
        })
        second = order._apply_tint(formula, self.base, 2)
        action = order.action_check_branch_stock()
        self.assertFalse(action['context']['default_sale_line_id'])
        original_labels = (first | second).mapped('display_name')
        labels = (first | second).with_context(stock_lookup_order_line=True).mapped('display_name')
        self.assertIn(self.color.name, labels[0])
        self.assertIn(color.name, labels[1])
        self.assertNotEqual(labels[0], labels[1])
        self.assertEqual((first | second).mapped('display_name'), original_labels)
        matches = self.env['sale.order.line'].with_context(stock_lookup_order_line=True).name_search(
            color.name, domain=[('order_id', '=', order.id)])
        self.assertIn(second.id, [record_id for record_id, _name in matches])
        with Form(self.env['product.stock.lookup'].with_context(**action['context'])) as form:
            form.sale_line_id = first
            form.warehouse_id = order.warehouse_id
        wizard = form.record
        self.assertEqual(wizard.product_id, self.base)
        self.assertEqual(wizard.requested_qty, 1)
        self.assertEqual(wizard.line_ids.filtered(lambda row: row.product_id == self.colorant).required_qty, 12)
        wizard.sale_line_id = second
        self.assertEqual(wizard.requested_qty, 2)
        self.assertEqual(wizard.line_ids.filtered(lambda row: row.product_id == self.colorant).required_qty, 16)
        self.assertEqual(order.order_line, first | second)
        other = self._order()._apply_tint(self.formula, self.base, 1)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            wizard.sale_line_id = other

    def test_stock_lookup_aggregates_frozen_components_without_changing_sale(self):
        order = self._order()
        first = order._apply_tint(self.formula, self.base, 1)
        self.formula.line_ids.points = 8
        second = order._apply_tint(self.formula, self.base, 2)
        self.formula.line_ids.points = 20
        before = order.order_line.read(['product_id', 'product_uom_qty', 'price_unit', 'tint_snapshot'])
        location = order.warehouse_id.lot_stock_id
        self.env['stock.quant']._update_available_quantity(self.base, location, 4)
        self.env['stock.quant']._update_available_quantity(self.colorant, location, 20)
        quant = self.env['stock.quant'].search([('product_id', '=', self.colorant.id), ('location_id', '=', location.id)])
        quant.reserved_quantity = 5
        wizard = self._stock_lookup(order, warehouse_id=order.warehouse_id.id)
        self.assertTrue(wizard.has_tint)
        self.assertEqual(len(wizard.line_ids), 2)
        base = wizard.line_ids.filtered(lambda row: row.product_id == self.base)
        colorant = wizard.line_ids - base
        self.assertEqual(base.required_qty, 3)
        self.assertEqual(base.component_kind, 'base')
        self.assertFalse(base.shortage)
        self.assertEqual(colorant.required_qty, 28)
        self.assertEqual(colorant.available, 15)
        self.assertTrue(colorant.shortage)
        self.assertIn(self.color.name, colorant.color_names)
        self.assertEqual(wizard.total_available, 4)
        self.assertEqual(order.order_line, first | second)
        self.assertEqual(order.order_line.read(['product_id', 'product_uom_qty', 'price_unit', 'tint_snapshot']), before)
        self.assertFalse(order.picking_ids)
        self.assertEqual(quant.quantity, 20)
        branch = self.env['stock.warehouse'].create({'name': 'Componentes sucursal QA', 'code': 'TCQA'})
        self.env['stock.quant']._update_available_quantity(self.colorant, branch.lot_stock_id, 100)
        wizard.warehouse_id = False
        rows = wizard.line_ids.filtered(lambda row: row.warehouse_id == branch)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.filtered(lambda row: row.product_id == self.base).available, 0)
        self.assertEqual(rows.filtered(lambda row: row.product_id == self.colorant).available, 100)
        self.assertEqual(wizard.total_available, 4)

    def test_stock_lookup_keeps_missing_components(self):
        order = self._order()
        order._apply_tint(self.formula, self.base, 2)
        self.env['stock.quant']._update_available_quantity(self.colorant, order.warehouse_id.lot_stock_id, 30)
        wizard = self._stock_lookup(order, warehouse_id=order.warehouse_id.id)
        self.assertEqual(len(wizard.line_ids), 2)
        base = wizard.line_ids.filtered(lambda row: row.product_id == self.base)
        self.assertEqual(base.available, 0)
        self.assertTrue(base.shortage)

    def test_stock_lookup_components_convert_to_inventory_unit(self):
        self.colorant.uom_id = self.env.ref('entintados_pdv.uom_tint_ounce')
        order, _location = self._prepare_delivery(containers=1, colorant_stock=10, confirm=False)
        wizard = self._stock_lookup(order, warehouse_id=order.warehouse_id.id)
        base = wizard.line_ids.filtered(lambda row: row.product_id == self.base)
        colorant = wizard.line_ids - base
        self.assertEqual(base.required_qty, 1)
        self.assertAlmostEqual(colorant.required_qty, 0.25)
        self.assertEqual(colorant.product_uom_id, self.colorant.uom_id)

    def test_only_base_product_with_notes_and_full_price(self):
        order = self._order()
        line = order._apply_tint(self.formula, self.base, 2)
        self.assertEqual(order.order_line, line)
        self.assertFalse(line.display_type)
        self.assertEqual(line.product_id, self.base)
        self.assertEqual(line.price_unit, 224)
        self.assertEqual(line.price_subtotal, 448)
        self.assertIn('Rojo Sangre prueba', line.name)
        color_description = '+ [%s] %s' % (self.color.code, self.color.name)
        self.assertEqual(line.name, self.base.display_name + '\n' + color_description)
        self.assertEqual(line.tint_display_name, self.base.display_name)
        self.assertEqual(line.tint_color_description, color_description)
        self.assertFalse(self.colorant.sale_ok)

    def test_normal_products_and_tints_stay_in_insertion_order(self):
        order = self._order()
        brush = self.env['product.product'].create({'name': 'Brocha', 'list_price': 50, 'taxes_id': [Command.clear()]})
        first = order._apply_tint(self.formula, self.base, 1)
        regular = self.env['sale.order.line'].create({'order_id': order.id, 'product_id': brush.id,
                                                   'sequence': first.sequence + 1})
        second = order._apply_tint(self.formula, self.base, 2)
        self.assertFalse(order.order_line.filtered('display_type'))
        self.assertEqual(order.order_line.sorted('sequence'), first | regular | second)
        self.assertEqual(order.amount_untaxed, 722)

    def test_two_tints_can_share_base(self):
        order = self._order()
        first = order._apply_tint(self.formula, self.base, 1)
        second = order._apply_tint(self.formula, self.base, 2)
        self.assertEqual(order.order_line, first | second)
        self.assertEqual(order.amount_untaxed, 672)

    def test_recipe_and_prices_are_frozen(self):
        order = self._order()
        line = order._apply_tint(self.formula, self.base, 1)
        self.formula.line_ids.points = 20
        self.base.list_price = 999
        order._recompute_pricing_rules()
        self.assertEqual(order.amount_untaxed, 224)
        self.assertEqual(line._tint_recipe()['doses'][0]['points'], 12)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            line.product_uom_qty = 3

    def test_loose_colorants_and_forged_recipe_blocked(self):
        order = self._order()
        for values in ({'product_id': self.colorant.id},
                       {'product_id': self.base.id, 'tint_snapshot': {'base_only': True}}):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.env['sale.order.line'].create({'order_id': order.id, **values})

    def test_invalid_quantity_and_base(self):
        order = self._order()
        for quantity in (0, -1, 1.5):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                order._apply_tint(self.formula, self.base, quantity)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            order._apply_tint(self.formula, self.colorant, 1)

    def test_delete_base_directly_removes_only_that_tint(self):
        order = self._order()
        first = order._apply_tint(self.formula, self.base, 1)
        second = order._apply_tint(self.formula, self.base, 2)
        first.unlink()
        self.assertEqual(order.order_line, second)
        self.assertEqual(order.amount_untaxed, 448)

    def test_delete_whole_draft_quotation(self):
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        line_ids = order.order_line.ids
        order.unlink()
        self.assertFalse(order.exists())
        self.assertFalse(self.env['sale.order.line'].browse(line_ids).exists())

    def test_confirmed_base_cannot_be_deleted(self):
        order = self._order()
        line = order._apply_tint(self.formula, self.base, 1)
        order.state = 'sale'
        with self.assertRaises(ValidationError), self.cr.savepoint():
            line.unlink()

    def test_wizard_form_and_preview(self):
        order = self._order()
        with Form(self.env['sale.tint.wizard'].with_context(default_order_id=order.id)) as wizard:
            wizard.color_id = self.color
            self.assertEqual(wizard.formula_id, self.formula)
            self.assertEqual(wizard.base_id, self.base)
            wizard.quantity = 2
            self.assertEqual(wizard.amount_total, 448)
        wizard.record.action_apply()
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.amount_untaxed, 448)

    def test_button_stays_to_left_of_coupon(self):
        from lxml import etree
        arch = etree.fromstring(self.env['sale.order'].get_view(view_type='form')['arch'])
        button = arch.xpath('//button[@name="action_add_tint"]')[0]
        coupon_action = str(self.env.ref('sale_loyalty.sale_loyalty_coupon_wizard_action').id)
        following = [sibling.get('name') for sibling in button.itersiblings()]
        self.assertIn(coupon_action, following)

    def test_report_has_only_color_without_recipe_or_automatic_sections(self):
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        html, _ = self.env['ir.actions.report']._render_qweb_html('sale.action_report_saleorder', order.ids)
        self.assertIn('Rojo Sangre prueba', html.decode())
        self.assertNotIn('12 Pts.', html.decode())
        self.assertNotIn('Fórmula por envase:', html.decode())
        self.assertNotIn('Otros productos', html.decode())
        self.assertFalse(order.order_line.filtered('display_type'))

    def test_seller_can_add_existing_formula(self):
        seller = new_test_user(self.env, login='flat_tint_seller', groups='sales_team.group_sale_salesman')
        partner = self.env['res.partner'].create({'name': 'Cliente vendedor', 'is_customer': True,
                                                'phone': '5555555555', 'user_id': seller.id})
        order = self.env['sale.order'].with_user(seller).create({'partner_id': partner.id})
        self.env['sale.tint.wizard'].with_user(seller).create({
            'order_id': order.id, 'formula_id': self.formula.id, 'base_id': self.base.id, 'quantity': 1}).action_apply()
        self.assertEqual(order.amount_untaxed, 224)
        self.assertEqual(len(order.order_line), 1)

    def test_flat_base_can_be_reordered_without_changing_recipe(self):
        order = self._order()
        line = order._apply_tint(self.formula, self.base, 1)
        line.sequence = 99
        self.assertEqual(line.sequence, 99)
        self.assertEqual(line._tint_recipe()['doses'][0]['points'], 12)
        self.assertEqual(line.price_unit, 224)

    def _prepare_delivery(self, containers=2, colorant_stock=100, confirm=True):
        # Confirmar la venta valida la entrega en el mismo paso.
        order = self._order()
        location = order.warehouse_id.lot_stock_id
        self.env['stock.quant']._update_available_quantity(self.base, location, 100)
        if colorant_stock:
            self.env['stock.quant']._update_available_quantity(self.colorant, location, colorant_stock)
        order._apply_tint(self.formula, self.base, containers)
        if confirm:
            order.action_confirm()
        return order, location

    def _qty(self, product, location):
        return product.with_context(location=location.id).qty_available

    def test_delivery_consumes_colorants_without_sale_lines(self):
        order, location = self._prepare_delivery()
        picking = order.picking_ids
        self.assertEqual(picking.state, 'done')
        self.assertEqual(picking.move_ids.product_id, self.base | self.colorant)
        consumption = picking._tint_consumption_moves()
        self.assertEqual(consumption.product_id, self.colorant)
        self.assertEqual(consumption.quantity, 24)
        self.assertEqual(consumption.state, 'done')
        self.assertEqual(consumption.picking_id, picking)
        self.assertFalse(consumption.sale_line_id)
        self.assertFalse(order.order_line.filtered(lambda l: l.product_id == self.colorant))
        self.assertEqual(self._qty(self.colorant, location), 76)

    def test_delivery_native_validate_and_view(self):
        from odoo.tools.convert import convert_file
        convert_file(self.env, 'entintados_pdv', 'views/sale_tint_stock_views.xml', {}, mode='update')
        order, location = self._prepare_delivery()
        picking = order.picking_ids
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(picking.move_ids.product_id, self.base | self.colorant)
        self.assertEqual(picking._tint_consumption_moves().description_picking, self.colorant.display_name)
        self.assertFalse(order.order_line.filtered(lambda line: line.product_id == self.colorant))
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 76)

    def test_delivery_upgrade_reuses_existing_moves(self):
        order, location = self._prepare_delivery()
        picking = order.picking_ids
        moves = picking.move_ids
        self.env['stock.move']._setup_tint_delivery_components()
        self.env['stock.move']._setup_tint_delivery_components()
        self.assertEqual(picking.move_ids, moves)
        component = picking._tint_consumption_moves()
        component.picking_id = False
        self.env['stock.move']._setup_tint_delivery_components()
        self.assertEqual(component.picking_id, picking)
        self.assertEqual(self._qty(self.colorant, location), 76)

    def test_delivered_tint_invoice_and_material_consumption_are_consistent(self):
        self.base.invoice_policy = 'delivery'
        order, location = self._prepare_delivery()
        expected = self.base.display_name + ' + [%s] %s' % (self.color.code, self.color.name)
        picking = order.picking_ids
        self.assertEqual(picking.move_ids.filtered(lambda move: move.product_id == self.base).description_picking,
                         self.base.display_name)
        invoice = order._create_invoices()
        lines = invoice.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
        self.assertEqual(lines.product_id, self.base)
        self.assertEqual(lines.quantity, 2)
        self.assertEqual(lines.price_unit, 224)
        self.assertEqual(lines.name, expected)
        self.assertEqual(invoice.amount_untaxed, 448)
        self.assertEqual(self._qty(self.base, location), 98)
        self.assertEqual(self._qty(self.colorant, location), 76)

    def test_colorant_shortage_blocks_confirmation(self):
        order, location = self._prepare_delivery(colorant_stock=0, confirm=False)
        with self.assertRaises(UserError), self.cr.savepoint():
            order.action_confirm()
        self.assertIn(order.state, ('draft', 'sent'))
        self.assertEqual(self._qty(self.base, location), 100)
        self.assertFalse(self.env['stock.move'].search([
            ('product_id', '=', self.colorant.id), ('state', '=', 'done')]))

    def test_delivery_keeps_snapshot_after_formula_changes(self):
        order, location = self._prepare_delivery(confirm=False)
        self.formula.line_ids.points = 20
        order.action_confirm()
        self.assertEqual(self._qty(self.colorant, location), 76)

    def test_delivery_converts_points_to_ounce_inventory(self):
        self.colorant.uom_id = self.env.ref('entintados_pdv.uom_tint_ounce')
        self.colorant.list_price = 96
        __, location = self._prepare_delivery(colorant_stock=10)
        self.assertAlmostEqual(self._qty(self.colorant, location), 9.5)

    def test_two_tints_share_base_without_merging_their_recipes(self):
        order = self._order()
        location = order.warehouse_id.lot_stock_id
        for product in (self.base, self.colorant):
            self.env['stock.quant']._update_available_quantity(product, location, 100)
        order._apply_tint(self.formula, self.base, 1)
        self.formula.line_ids.points = 8
        order._apply_tint(self.formula, self.base, 1)
        order.action_confirm()
        self.assertEqual(self._qty(self.colorant, location), 80)
        self.assertEqual(sorted(order.picking_ids._tint_consumption_moves().mapped('quantity')), [8, 12])

    def test_return_restores_inventory_without_selling_colorants(self):
        order, location = self._prepare_delivery()
        delivery = order.picking_ids
        self.assertEqual(delivery.state, 'done')
        wizard = self.env['stock.return.picking'].create({'picking_id': delivery.id})
        self.assertNotIn(self.colorant, wizard.product_return_moves.product_id)
        wizard.product_return_moves.filtered(lambda line: line.product_id == self.base).quantity = 2
        returned = wizard._create_return()
        for move in returned.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        returned.with_context(skip_stock_return_credit=True)._action_done()
        self.assertEqual(self._qty(self.colorant, location), 100)
        self.assertFalse(order.order_line.filtered(lambda line: line.product_id == self.colorant))
        self.assertEqual(order.amount_untaxed, 448)

    def test_consumption_cannot_be_forged(self):
        order, _location = self._prepare_delivery()
        base_move = order.picking_ids.move_ids.filtered(lambda move: move.product_id == self.base)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            base_move.copy({'tint_base_move_id': base_move.id})
