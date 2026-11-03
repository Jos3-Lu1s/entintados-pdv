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

    def test_only_base_product_with_notes_and_full_price(self):
        order = self._order()
        line = order._apply_tint(self.formula, self.base, 2)
        self.assertEqual(order.order_line, line)
        self.assertFalse(line.display_type)
        self.assertFalse(line.tint_section_id)
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
        self.assertFalse(order.order_line.filtered('tint_area'))
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
        with self.assertRaises(ValidationError), self.cr.savepoint():
            order._apply_tint(self.formula, self.base, 3, line)

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
        self.assertEqual(button.getnext().get('name'), str(self.env.ref('sale_loyalty.sale_loyalty_coupon_wizard_action').id))

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

    def _legacy_layout(self, order, quantity=1, with_colorant_lines=False):
        from ..models.sale_tint import _TINT_OPERATION
        internal = {'_tint_operation': _TINT_OPERATION}
        base_line = order._apply_tint(self.formula, self.base, quantity)
        snapshot = dict(base_line.tint_snapshot)
        snapshot['base_only'] = not with_colorant_lines
        header = self.env['sale.order.line'].with_context(**internal).create({
            'order_id': order.id, 'display_type': 'line_subsection', 'name': 'Entintado anterior',
            'tint_snapshot': snapshot, 'tint_container_qty': quantity})
        base_line.with_context(**internal).write({'tint_section_id': header.id,
            'tint_snapshot': False, 'tint_container_qty': 0,
            'price_unit': 200 if with_colorant_lines else 224,
            'technical_price_unit': 200 if with_colorant_lines else 224})
        if with_colorant_lines:
            self.env['sale.order.line'].with_context(**internal).create({
                'order_id': order.id, 'product_id': self.colorant.id, 'product_uom_qty': 12 * quantity,
                'product_uom_id': self.point.id, 'price_unit': 2, 'tint_section_id': header.id})
        return base_line, header

    def test_upgrade_multiple_headers_preserves_base_ids_recipe_and_total(self):
        from ..models.sale_tint import _TINT_OPERATION
        order = self._order()
        first, _h1 = self._legacy_layout(order, 1)
        second, _h2 = self._legacy_layout(order, 2)
        for area in ('tints', 'products'):
            self.env['sale.order.line'].with_context(_tint_operation=_TINT_OPERATION).create({
                'order_id': order.id, 'display_type': 'line_section', 'name': area, 'tint_area': area})
        self.env['sale.order']._setup_sale_tint_layout()
        self.env['sale.order']._setup_sale_tint_layout()
        self.assertEqual(set(order.order_line.ids), set((first | second).ids))
        self.assertFalse(order.order_line.filtered('display_type'))
        self.assertFalse(order.order_line.filtered('tint_section_id'))
        self.assertEqual(first._tint_recipe()['doses'][0]['points'], 12)
        self.assertEqual(order.amount_untaxed, 672)

    def test_upgrade_old_component_lines_into_flat_base(self):
        order = self._order()
        base_line, _header = self._legacy_layout(order, with_colorant_lines=True)
        self.assertEqual(order.amount_untaxed, 224)
        self.env['sale.order']._setup_sale_tint_layout()
        self.assertEqual(order.order_line, base_line)
        self.assertTrue(base_line.tint_snapshot['base_only'])
        self.assertEqual(order.amount_untaxed, 224)
        self.assertFalse(base_line.tint_section_id)

    def test_upgrade_keeps_manually_created_sections(self):
        order = self._order()
        self._legacy_layout(order)
        manual = self.env['sale.order.line'].create({'order_id': order.id, 'display_type': 'line_section',
                                                    'name': 'Sección del usuario'})
        self.env['sale.order']._setup_sale_tint_layout()
        self.assertTrue(manual.exists())
        self.assertEqual(order.order_line.filtered('display_type'), manual)

    def test_description_upgrade_preserves_recipe_price_and_confirmed_documents(self):
        from ..models.sale_tint import _TINT_OPERATION
        order = self._order()
        line = order._apply_tint(self.formula, self.base, 2)
        line.with_context(_tint_operation=_TINT_OPERATION).write({'name': 'Entintado anterior\nFórmula por envase: 12 Pts.'})
        snapshot = dict(line.tint_snapshot)
        self.env['sale.order']._setup_sale_tint_layout()
        self.assertEqual(line.name, self.base.display_name + '\n+ [%s] %s' % (self.color.code, self.color.name))
        self.assertEqual(line.tint_snapshot, snapshot)
        self.assertEqual(line.price_unit, 224)
        self.assertEqual(line.product_uom_qty, 2)
        confirmed, _location = self._prepare_delivery()
        old_name = confirmed.order_line.name
        self.env['sale.order']._setup_sale_tint_layout()
        self.assertEqual(confirmed.order_line.name, old_name)

    def test_legacy_color_description_tolerates_missing_fields(self):
        order = self._order()
        self.assertEqual(order._tint_color_description({'name': '[OLD-01] Color anterior — Base anterior'}),
                         '[OLD-01] Color anterior')
        self.assertEqual(order._tint_color_description({'color_name': 'Solo nombre'}), 'Solo nombre')
        self.assertEqual(order._tint_color_description({}), '')

    def test_flat_base_can_be_reordered_without_changing_recipe(self):
        order = self._order()
        line = order._apply_tint(self.formula, self.base, 1)
        line.sequence = 99
        self.assertEqual(line.sequence, 99)
        self.assertEqual(line._tint_recipe()['doses'][0]['points'], 12)
        self.assertEqual(line.price_unit, 224)

    def _prepare_delivery(self, containers=2, colorant_stock=100):
        order = self._order()
        location = order.warehouse_id.lot_stock_id
        self.env['stock.quant']._update_available_quantity(self.base, location, 100)
        self.env['stock.quant']._update_available_quantity(self.colorant, location, colorant_stock)
        order._apply_tint(self.formula, self.base, containers)
        order.action_confirm()
        return order, location

    def _deliver(self, picking, containers):
        move = picking.move_ids.filtered(lambda m: m.product_id == self.base and m.state not in ('done', 'cancel'))
        move.quantity = containers
        move.picked = True
        picking._action_done()

    def test_delivery_consumes_colorants_without_sale_lines(self):
        order, location = self._prepare_delivery()
        picking = order.picking_ids
        self._deliver(picking, 2)
        consumption = picking._tint_consumption_moves()
        self.assertEqual(consumption.product_id, self.colorant)
        self.assertEqual(consumption.quantity, 24)
        self.assertEqual(consumption.state, 'done')
        self.assertFalse(consumption.sale_line_id)
        self.assertFalse(order.order_line.filtered(lambda l: l.product_id == self.colorant))
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 76)
        picking.move_ids._action_done()
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 76)

    def test_partial_delivery_invoice_and_material_consumption_are_consistent(self):
        self.base.invoice_policy = 'delivery'
        order, location = self._prepare_delivery()
        expected = self.base.display_name + '\n+ [%s] %s' % (self.color.code, self.color.name)
        picking = order.picking_ids
        self.assertEqual(picking.move_ids.filtered(lambda move: move.product_id == self.base).description_picking,
                         '+ [%s] %s' % (self.color.code, self.color.name))
        self._deliver(picking, 1)
        first = order._create_invoices()
        lines = first.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
        self.assertEqual(lines.product_id, self.base)
        self.assertEqual(lines.quantity, 1)
        self.assertEqual(lines.price_unit, 224)
        self.assertEqual(lines.name, expected)
        self.assertEqual(self.base.with_context(location=location.id).qty_available, 99)
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 88)
        backorder = order.picking_ids.filtered(lambda item: item.backorder_id == picking)
        self._deliver(backorder, 1)
        second = order._create_invoices()
        self.assertEqual(second.invoice_line_ids.filtered(lambda line: line.display_type == 'product').quantity, 1)
        self.assertEqual(self.base.with_context(location=location.id).qty_available, 98)
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 76)
        self.assertEqual(sum((first | second).mapped('amount_untaxed')), 448)

    def test_partial_delivery_and_backorder_consume_proportionally(self):
        order, location = self._prepare_delivery()
        first = order.picking_ids
        self._deliver(first, 1)
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 88)
        backorder = order.picking_ids.filtered(lambda p: p.backorder_id == first)
        self.assertTrue(backorder)
        self._deliver(backorder, 1)
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 76)
        self.assertEqual(sum(order.picking_ids._tint_consumption_moves().mapped('quantity')), 24)

    def test_shortage_at_delivery_rolls_back_base_and_colorants(self):
        order, location = self._prepare_delivery()
        self.env['stock.quant']._update_available_quantity(self.colorant, location, -100)
        picking = order.picking_ids
        with self.assertRaises(UserError), self.cr.savepoint():
            self._deliver(picking, 2)
        self.assertNotEqual(picking.state, 'done')
        self.assertEqual(self.base.with_context(location=location.id).qty_available, 100)
        self.assertFalse(picking._tint_consumption_moves())

    def test_cancel_before_delivery_does_not_consume_colorants(self):
        order, location = self._prepare_delivery()
        order._action_cancel()
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 100)
        self.assertFalse(order.picking_ids._tint_consumption_moves())

    def test_delivery_keeps_snapshot_after_formula_changes(self):
        order, location = self._prepare_delivery()
        self.formula.line_ids.points = 20
        self._deliver(order.picking_ids, 2)
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 76)

    def test_delivery_converts_points_to_ounce_inventory(self):
        self.colorant.uom_id = self.env.ref('entintados_pdv.uom_tint_ounce')
        self.colorant.list_price = 96
        order, location = self._prepare_delivery(colorant_stock=10)
        self._deliver(order.picking_ids, 2)
        self.assertAlmostEqual(self.colorant.with_context(location=location.id).qty_available, 9.5)

    def test_two_tints_share_base_without_merging_their_recipes(self):
        order = self._order()
        location = order.warehouse_id.lot_stock_id
        for product in (self.base, self.colorant):
            self.env['stock.quant']._update_available_quantity(product, location, 100)
        order._apply_tint(self.formula, self.base, 1)
        self.formula.line_ids.points = 8
        order._apply_tint(self.formula, self.base, 1)
        order.action_confirm()
        self._deliver(order.picking_ids, 1)
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 80)
        self.assertEqual(sorted(order.picking_ids._tint_consumption_moves().mapped('quantity')), [8, 12])

    def test_return_restores_inventory_without_selling_colorants(self):
        order, location = self._prepare_delivery()
        delivery = order.picking_ids
        self._deliver(delivery, 2)
        wizard = self.env['stock.return.picking'].create({'picking_id': delivery.id})
        self.assertNotIn(self.colorant, wizard.product_return_moves.product_id)
        wizard.product_return_moves.filtered(lambda line: line.product_id == self.base).quantity = 2
        returned = wizard._create_return()
        for move in returned.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        returned.with_context(skip_stock_return_credit=True)._action_done()
        self.assertEqual(self.colorant.with_context(location=location.id).qty_available, 100)
        self.assertFalse(order.order_line.filtered(lambda line: line.product_id == self.colorant))
        self.assertEqual(order.amount_untaxed, 448)

    def test_consumption_cannot_be_forged(self):
        order, _location = self._prepare_delivery()
        base_move = order.picking_ids.move_ids
        with self.assertRaises(ValidationError), self.cr.savepoint():
            base_move.copy({'tint_base_move_id': base_move.id})
