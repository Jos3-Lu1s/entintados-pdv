from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPosSaleQuotation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Cliente cotización caja', 'phone': '5551234567', 'is_customer': True})
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'Cotización caja QA', 'currency_id': cls.env.company.currency_id.id})
        cls.config = cls.env['pos.config'].create({'name': 'Cotizaciones POS QA', 'pricelist_id': cls.pricelist.id})
        cls.session = cls.env['pos.session'].create({'config_id': cls.config.id, 'state': 'opened'})
        cls.product = cls.env['product.product'].create({'name': 'Brocha cotización QA', 'list_price': 100,
            'available_in_pos': True, 'is_storable': True, 'taxes_id': [Command.clear()]})
        cls.point = cls.env.ref('entintados_pdv.uom_tint_point')
        cls.base = cls.env['product.product'].create({'name': 'Base cotización caja QA', 'tint_role': 'base',
            'tint_base_type_id': cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1).id,
            'tint_size_id': cls.env['tint.size'].search([], limit=1).id, 'available_in_pos': True,
            'is_storable': True, 'taxes_id': [Command.clear()]})
        cls.colorant = cls.env['product.product'].create({'name': 'Colorante caja QA', 'tint_role': 'colorant',
            'uom_id': cls.point.id, 'is_storable': True, 'taxes_id': [Command.clear()]})

    def _ticket(self, tinted=False, discount=0, taxes=None):
        product = self.base if tinted else self.product
        price = 224 if tinted else 100
        taxes = taxes or self.env['account.tax']
        totals = taxes.compute_all(price * (1 - discount / 100), self.env.company.currency_id, 2, product=product)
        order = self.env['pos.order'].create({'session_id': self.session.id, 'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id, 'amount_total': totals['total_included'],
            'amount_tax': totals['total_included'] - totals['total_excluded'], 'amount_paid': 0, 'amount_return': 0,
            'lines': [Command.create({'product_id': product.id, 'qty': 2, 'price_unit': price,
                'discount': discount, 'price_subtotal': totals['total_excluded'], 'price_subtotal_incl': totals['total_included'],
                'tax_ids': [Command.set(taxes.ids)], 'tint_color_code': 'QA-ROJO' if tinted else False,
                'customer_note': 'Color: Rojo caja QA' if tinted else 'Para fachada'})]})
        if tinted:
            self.env['pos.order.line'].create({'order_id': order.id, 'product_id': self.colorant.id,
                'qty': 24, 'price_unit': 0, 'price_subtotal': 0, 'price_subtotal_incl': 0,
                'combo_parent_id': order.lines.id, 'tax_ids': [Command.clear()]})
        return order

    def test_regular_ticket_creates_unpaid_draft_and_is_idempotent(self):
        ticket = self._ticket(discount=10)
        result = ticket.action_create_sale_quotation()
        quote = self.env['sale.order'].browse(result['id'])
        self.assertEqual(quote.state, 'draft')
        self.assertEqual(quote.partner_id, ticket.partner_id)
        self.assertEqual(quote.order_line.product_id, self.product)
        self.assertEqual(quote.amount_total, 180)
        self.assertEqual(quote.order_line.discount, 10)
        self.assertIn('fachada', quote.order_line.name)
        self.assertFalse(quote.picking_ids | ticket.picking_ids)
        self.assertFalse(quote.invoice_ids)
        self.assertEqual(ticket.state, 'cancel')
        self.assertEqual(ticket.action_create_sale_quotation(), result)
        self.assertEqual(ticket.get_generated_sale_quotation(), result)
        with self.assertRaises(UserError):
            ticket.write({'state': 'draft'})

    def test_quotation_ignores_default_sale_state(self):
        ticket = self._ticket()
        ticket.with_context(default_state='sale').action_create_sale_quotation()
        self.assertEqual(ticket.generated_quotation_id.state, 'draft')

    def test_ui_retry_recovers_quotation_by_uuid_without_syncing_cancelled_ticket(self):
        ticket = self._ticket()
        expected = ticket.action_create_sale_quotation()
        result = self.env['pos.order'].create_sale_quotation_from_ui({'uuid': ticket.uuid, 'state': 'draft'})
        self.assertEqual(result, expected)

    def test_ui_rejects_paid_order_before_sync(self):
        ticket = self._ticket()
        with self.assertRaises(UserError):
            self.env['pos.order'].create_sale_quotation_from_ui({'uuid': ticket.uuid, 'state': 'paid'})
        self.assertFalse(ticket.generated_quotation_id)

    def test_tinted_ticket_keeps_price_discount_and_frozen_doses(self):
        ticket = self._ticket(tinted=True, discount=10)
        ticket.action_create_sale_quotation()
        quote = ticket.generated_quotation_id
        self.assertEqual(len(quote.order_line), 1)
        self.assertEqual(quote.order_line.product_id, self.base)
        self.assertAlmostEqual(quote.amount_total, 403.2)
        self.assertEqual(quote.order_line._tint_recipe()['doses'][0]['points'], 12)
        self.assertIn('Rojo caja QA', quote.order_line.name)
        quote._recompute_pricing_rules()
        self.assertAlmostEqual(quote.amount_total, 403.2)
        self.assertFalse(ticket.picking_ids | quote.picking_ids)

    def test_tax_amounts_preserved(self):
        tax = self.env['account.tax'].create({'name': 'IVA caja QA', 'amount': 16, 'type_tax_use': 'sale'})
        ticket = self._ticket(taxes=tax)
        ticket.action_create_sale_quotation()
        self.assertEqual(ticket.generated_quotation_id.amount_total, 232)
        self.assertEqual(ticket.generated_quotation_id.amount_tax, 32)

    def test_manual_ticket_price_survives_sales_recalculation(self):
        ticket = self._ticket()
        ticket.lines.write({'price_unit': 85, 'price_subtotal': 170, 'price_subtotal_incl': 170})
        ticket.amount_total = 170
        ticket.action_create_sale_quotation()
        ticket.generated_quotation_id._recompute_pricing_rules()
        self.assertEqual(ticket.generated_quotation_id.amount_total, 170)

    def test_empty_quotation_reference_from_pos_sync_is_allowed(self):
        ticket = self._ticket()
        ticket.write({'generated_quotation_id': False, 'amount_total': 200})
        self.assertFalse(ticket.generated_quotation_id)
        ticket.action_create_sale_quotation()
        self.assertTrue(ticket.generated_quotation_id)

    def test_pos_without_pricelists_creates_and_reuses_currency_bridge(self):
        ticket = self._ticket()
        ticket.pricelist_id = False
        self.config.pricelist_id = False
        lists = self.env['product.pricelist'].search([
            ('currency_id', '=', ticket.currency_id.id),
            '|', ('company_id', '=', False), ('company_id', '=', ticket.company_id.id)])
        lists.active = False
        ticket.action_create_sale_quotation()
        bridge = ticket.generated_quotation_id.pricelist_id
        self.assertEqual(bridge.currency_id, ticket.currency_id)
        self.assertEqual(bridge.company_id, ticket.company_id)
        self.assertEqual(ticket.generated_quotation_id.amount_total, 200)
        self.assertFalse(self.config.pricelist_id)
        another = self._ticket()
        another.pricelist_id = False
        another.action_create_sale_quotation()
        self.assertEqual(another.generated_quotation_id.pricelist_id, bridge)

    def test_no_customer_and_closed_session_rejected(self):
        ticket = self._ticket()
        ticket.partner_id = False
        with self.assertRaises(UserError):
            ticket.action_create_sale_quotation()
        ticket.partner_id = self.partner
        self.session.state = 'closing_control'
        with self.assertRaises(UserError):
            ticket.action_create_sale_quotation()
        self.assertFalse(ticket.generated_quotation_id)

    def test_paid_or_existing_sale_link_rejected(self):
        ticket = self._ticket()
        ticket.amount_paid = 1
        with self.assertRaises(UserError):
            ticket.action_create_sale_quotation()
        ticket.amount_paid = 0
        quote = self.env['sale.order'].create({'partner_id': self.partner.id})
        ticket.lines.sale_order_origin_id = quote
        with self.assertRaises(UserError):
            ticket.action_create_sale_quotation()

    def test_no_sales_rights_and_forged_reference_rejected(self):
        ticket = self._ticket()
        user = new_test_user(self.env, login='pos_quotation_no_sales', groups='base.group_user,point_of_sale.group_pos_user')
        with self.assertRaises(AccessError):
            ticket.with_user(user).action_create_sale_quotation()
        with self.assertRaises(UserError):
            ticket.generated_quotation_id = self.env['sale.order'].create({'partner_id': self.partner.id})

    def test_cashier_with_sales_rights_can_create_and_recover_native_quotation(self):
        ticket = self._ticket(tinted=True)
        user = new_test_user(self.env, login='pos_quotation_sales_user',
                             groups='base.group_user,point_of_sale.group_pos_user,sales_team.group_sale_salesman')
        ticket.partner_id = self.env['res.partner'].create({'name': 'Cliente del cajero QA',
            'phone': '5555551234', 'is_customer': True, 'user_id': user.id})
        result = ticket.with_user(user).action_create_sale_quotation()
        quote = self.env['sale.order'].browse(result['id']).with_user(user)
        self.assertEqual(quote.user_id, user)
        self.assertNotIn('accounting_return_ids', ticket.with_user(user)._load_pos_data_fields(self.config))
        data = quote.load_sale_order_from_pos(self.config.id)
        self.assertTrue(data['sale.order.line'][0]['tint_snapshot']['base_only'])

    def test_total_mismatch_rolls_back_quote_and_keeps_ticket(self):
        ticket = self._ticket()
        ticket.amount_total = 999
        before = self.env['sale.order'].search_count([])
        with self.assertRaises(UserError):
            ticket.action_create_sale_quotation()
        self.assertEqual(self.env['sale.order'].search_count([]), before)
        self.assertEqual(ticket.state, 'draft')

    def test_outdated_ticket_snapshot_cannot_quote_old_lines(self):
        ticket = self._ticket()
        line = ticket.lines
        expected = {'partner_id': self.partner.id, 'pricelist_id': self.pricelist.id,
                    'fiscal_position_id': False,
                    'lines': [[line.uuid, self.product.id, 3, 100, 0, False, 'Para fachada', False]]}
        with self.assertRaises(UserError):
            ticket.action_create_sale_quotation(expected)
        self.assertEqual(ticket.state, 'draft')
        self.assertFalse(ticket.generated_quotation_id)
        expected['lines'][0][2] = 2
        result = ticket.action_create_sale_quotation(expected)
        self.assertEqual(ticket.get_generated_sale_quotation(expected), result)

    def test_tint_delivery_from_sales_consumes_only_at_delivery(self):
        ticket = self._ticket(tinted=True)
        ticket.action_create_sale_quotation()
        quote = ticket.generated_quotation_id
        location = quote.warehouse_id.lot_stock_id
        self.env['stock.quant']._update_available_quantity(self.base, location, 2)
        self.env['stock.quant']._update_available_quantity(self.colorant, location, 24)
        quote.action_confirm()
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.colorant, location), 24)
        picking = quote.picking_ids
        picking.move_ids.quantity = 2
        picking.move_ids.picked = True
        picking.with_context(skip_sms=True, skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(picking._tint_consumption_moves().quantity, 24)

    def test_native_pos_pickings_consume_tint_from_quotation(self):
        ticket = self._ticket(tinted=True)
        ticket.action_create_sale_quotation()
        quote = ticket.generated_quotation_id
        location = self.config.picking_type_id.default_location_src_id
        self.env['stock.quant']._update_available_quantity(self.base, location, 2)
        self.env['stock.quant']._update_available_quantity(self.colorant, location, 24)
        recovered = self._ticket()
        recovered.lines.write({'product_id': self.base.id, 'price_unit': 224,
                               'sale_order_line_id': quote.order_line.id, 'sale_order_origin_id': quote.id})
        recovered.state = 'paid'
        recovered._create_order_picking()
        self.assertEqual(recovered.picking_ids.state, 'done')
        self.assertEqual(recovered.picking_ids._tint_consumption_moves().quantity, 24)
        self.assertEqual(len(recovered.picking_ids.move_ids), 1)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.colorant, location), 0)
        self.assertEqual(quote.order_line.qty_delivered, 2)
        quote.action_confirm()
        self.assertEqual(quote.state, 'sale')
