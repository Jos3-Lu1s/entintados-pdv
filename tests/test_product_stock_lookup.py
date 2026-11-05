from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tests import Form
from odoo.tools.convert import convert_file


@tagged('post_install', '-at_install')
class TestProductStockLookup(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({'name': 'Consulta inventario QA', 'is_storable': True})
        cls.other = cls.env['product.product'].create({'name': 'Otro producto QA', 'is_storable': True})
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.second = cls.env['stock.warehouse'].create({'name': 'Consulta segundo almacén QA', 'code': 'CSQA'})
        cls.shelf = cls.env['stock.location'].create({'name': 'Estante QA', 'usage': 'internal',
            'location_id': cls.warehouse.lot_stock_id.id, 'company_id': cls.env.company.id})
        cls.env['stock.quant']._update_available_quantity(cls.product, cls.shelf, 10)
        cls.env['stock.quant']._update_available_quantity(cls.other, cls.shelf, 99)
        quant = cls.env['stock.quant'].search([('product_id', '=', cls.product.id), ('location_id', '=', cls.shelf.id)])
        quant.reserved_quantity = 3

    def _wizard(self, **values):
        return self.env['product.stock.lookup'].create({'product_id': self.product.id, **values})

    def test_only_one_product_and_internal_child_locations(self):
        wizard = self._wizard(warehouse_id=self.warehouse.id)
        self.assertEqual(len(wizard.line_ids), 1)
        line = wizard.line_ids
        self.assertEqual(line.on_hand, 10)
        self.assertEqual(line.reserved, 3)
        self.assertEqual(line.available, 7)
        self.assertEqual(wizard.total_available, 7)

    def test_all_branches_and_product_change(self):
        wizard = self._wizard()
        self.assertIn(self.second, wizard.line_ids.warehouse_id)
        self.assertEqual(wizard.line_ids.filtered(lambda row: row.warehouse_id == self.second).available, 0)
        wizard.product_id = self.other
        self.assertEqual(wizard.total_on_hand, 99)
        self.assertEqual(wizard.total_reserved, 0)

    def test_refresh_reads_current_quantities(self):
        wizard = self._wizard(warehouse_id=self.warehouse.id)
        self.assertEqual(wizard.line_ids.on_hand, 10)
        self.env['stock.quant']._update_available_quantity(self.product, self.shelf, 5)
        action = wizard.action_refresh()
        self.assertEqual(action['res_id'], wizard.id)
        self.assertEqual(wizard.line_ids.on_hand, 15)
        self.assertEqual(wizard.line_ids.available, 12)

    def test_sale_and_product_actions_select_product(self):
        partner = self.env['res.partner'].create({'name': 'Consulta cliente QA', 'is_customer': True, 'phone': '5551234567'})
        order = self.env['sale.order'].create({'partner_id': partner.id})
        line = self.env['sale.order.line'].create({'order_id': order.id, 'product_id': self.product.id})
        action = line.action_view_stock_by_warehouse()
        self.assertEqual(action['context']['default_product_id'], self.product.id)
        self.assertEqual(action['context']['default_sale_order_id'], order.id)
        self.assertEqual(action['target'], 'new')
        template_action = self.product.product_tmpl_id.action_view_stock_by_warehouse()
        self.assertEqual(template_action['context']['default_product_id'], self.product.id)

    def test_salesperson_can_consult_without_sudo(self):
        user = new_test_user(self.env, login='stock_lookup_sales_qa', groups='base.group_user,sales_team.group_sale_salesman')
        wizard = self.env['product.stock.lookup'].with_user(user).create({
            'product_id': self.product.id, 'company_id': self.env.company.id, 'warehouse_id': self.warehouse.id})
        self.assertEqual(wizard.line_ids.available, 7)

    def test_disabled_company_and_services_rejected(self):
        company = self.env['res.company'].create({'name': 'Consulta compañía ajena QA'})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['product.stock.lookup'].with_context(allowed_company_ids=self.env.company.ids).create({
                'product_id': self.product.id, 'company_id': company.id})
        service = self.env['product.product'].create({'name': 'Servicio consulta QA', 'type': 'service'})
        with self.assertRaises(UserError):
            service.action_view_stock_by_warehouse()

    def _order(self, quantity=12):
        partner = self.env['res.partner'].create({'name': 'Consulta orden QA', 'is_customer': True, 'phone': '5551234567'})
        return self.env['sale.order'].create({'partner_id': partner.id, 'warehouse_id': self.warehouse.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': quantity})]})

    def test_order_button_replaces_inventory_menu_and_line_icons(self):
        convert_file(self.env, 'entintados_pdv', 'wizards/product_stock_lookup_views.xml', {}, mode='update')
        self.assertFalse(self.env.ref('entintados_pdv.menu_product_stock_lookup').active)
        arch = self.env.ref('sale.view_order_form')._get_combined_arch()
        self.assertTrue(arch.xpath("//header/button[@name='action_check_branch_stock']"))
        self.assertFalse(arch.xpath("//field[@name='order_line']//button[@name='action_view_stock_by_warehouse']"))
        order = self._order()
        action = order.action_check_branch_stock()
        self.assertEqual(action['context']['default_sale_order_id'], order.id)
        with Form(self.env['product.stock.lookup'].with_context(**action['context'])) as form:
            self.assertEqual(form.product_id, self.product)
        wizard = form.record
        self.assertEqual(wizard.order_product_ids, self.product)
        self.assertEqual(wizard.requested_qty, 12)
        self.assertEqual(wizard.order_warehouse_id, self.warehouse)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            wizard.product_id = self.other

    def test_shortage_reports_other_branches_and_keeps_draft(self):
        order = self._order()
        self.env['stock.quant']._update_available_quantity(self.product, self.second.lot_stock_id, 20)
        with self.assertRaises(UserError) as error:
            order.action_confirm()
        message = str(error.exception)
        self.assertIn(self.warehouse.display_name, message)
        self.assertIn(self.second.display_name, message)
        self.assertIn('cubre lo solicitado', message)
        self.assertIn('20', message)
        self.assertEqual(order.state, 'draft')

    def test_shortage_without_other_stock(self):
        with self.assertRaises(UserError) as error:
            self._order().action_confirm()
        self.assertIn('Sin stock disponible en otras sucursales', str(error.exception))

    def test_available_order_confirms_normally(self):
        order = self._order(quantity=2)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
