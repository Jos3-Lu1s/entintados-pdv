from uuid import uuid4

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.entintados_pdv.models.sale_tint import _TINT_OPERATION


@tagged('post_install', '-at_install')
class TestPosSaleOrderImport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['loyalty.program'].search([]).active = False
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa traer cotización QA', 'currency_id': cls.env.company.currency_id.id,
        })
        cash_journal = cls.env['account.journal'].search([
            ('type', '=', 'cash'), ('company_id', '=', cls.env.company.id)], limit=1)
        cls.cash = cls.env['pos.payment.method'].create({
            'name': 'Efectivo traer cotización QA', 'journal_id': cash_journal.id,
        })
        cls.config = cls.env['pos.config'].create({
            'name': 'Traer cotización QA', 'pricelist_id': cls.pricelist.id,
            'payment_method_ids': [Command.set(cls.cash.ids)],
        })
        cls.session = cls.env['pos.session'].create({'config_id': cls.config.id, 'state': 'opened'})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente traer cotización QA', 'phone': '5551234567', 'is_customer': True,
        })
        cls.partner.property_product_pricelist = cls.pricelist
        cls.product = cls._product('Brocha traer cotización QA', 100.0)
        cls.gift = cls._product('Regalo traer cotización QA', 50.0)

    @classmethod
    def _product(cls, name, price, **values):
        return cls.env['product.product'].create({
            'name': name, 'list_price': price, 'invoice_policy': 'order', 'available_in_pos': True,
            'taxes_id': [Command.clear()], **values,
        })

    def _quote(self, *products):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id, 'pricelist_id': self.pricelist.id,
            'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 2})
                           for product in products],
        })

    def test_regular_quotation_loads(self):
        quote = self._quote(self.product)
        result = quote.load_sale_order_from_pos(self.config.id)
        self.assertEqual([order['id'] for order in result['sale.order']], quote.ids)

    def test_quotation_with_combo_is_rejected(self):
        combo = self.env['product.combo'].create({
            'name': 'Combo traer cotización QA',
            'combo_item_ids': [Command.create({'product_id': self.product.id})],
        })
        package = self._product('Paquete traer cotización QA', 300.0, type='combo',
                                combo_ids=[Command.set(combo.ids)])
        quote = self._quote(self.product)
        package_line = self.env['sale.order.line'].create({
            'order_id': quote.id, 'product_id': package.id, 'product_uom_qty': 1,
        })
        self.env['sale.order.line'].create({
            'order_id': quote.id, 'product_id': self.product.id, 'product_uom_qty': 1,
            'combo_item_id': combo.combo_item_ids.id, 'linked_line_id': package_line.id,
        })
        with self.assertRaisesRegex(UserError, 'trae combos o regalos de promoción'):
            quote.load_sale_order_from_pos(self.config.id)

    def test_quotation_with_gift_is_rejected(self):
        program = self.env['loyalty.program'].create({
            'name': 'Regalo traer cotización QA', 'program_type': 'promotion', 'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [Command.clear(), Command.create({
                'product_ids': [Command.set(self.product.ids)], 'minimum_qty': 2,
                'reward_point_mode': 'order', 'reward_point_amount': 1,
            })],
            'reward_ids': [Command.clear(), Command.create({
                'reward_type': 'product', 'reward_product_id': self.gift.id,
                'reward_product_qty': 1, 'required_points': 1,
            })],
        })
        quote = self._quote(self.product)
        quote._update_programs_and_rewards()
        coupon = quote.coupon_point_ids.coupon_id.filtered(lambda card: card.program_id == program)
        self.assertNotIn('error', quote._apply_program_reward(program.reward_ids, coupon))
        self.assertTrue(quote.order_line.filtered('reward_id'))
        with self.assertRaisesRegex(UserError, 'trae combos o regalos de promoción'):
            quote.load_sale_order_from_pos(self.config.id)

    # --- Ventas queda con lo cobrado -------------------------------------

    def _pos_line(self, sale_line, price, discount=0.0, qty=None, **values):
        qty = sale_line.product_uom_qty if qty is None else qty
        subtotal = price * qty * (1 - discount / 100)
        return (0, 0, {
            'uuid': str(uuid4()), 'product_id': sale_line.product_id.id, 'qty': qty,
            'price_unit': price, 'discount': discount, 'price_subtotal': subtotal,
            'price_subtotal_incl': subtotal, 'tax_ids': [],
            'sale_order_origin_id': sale_line.order_id.id, 'sale_order_line_id': sale_line.id,
            **values,
        })

    def _charge(self, lines, state='paid'):
        total = sum(line[2]['price_subtotal_incl'] for line in lines)
        paid = state == 'paid'
        order = {
            'uuid': str(uuid4()), 'session_id': self.session.id, 'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id, 'state': state, 'lines': lines,
            'amount_tax': 0.0, 'amount_total': total, 'amount_return': 0.0,
            'amount_paid': total if paid else 0.0,
            'payment_ids': [(0, 0, {'payment_method_id': self.cash.id, 'amount': total})] if paid else [],
        }
        result = self.env['pos.order'].sync_from_ui([order])
        return self.env['pos.order'].browse(result['pos.order'][0]['id'])

    def test_draft_quotation_takes_charged_prices_before_confirmation(self):
        quote = self._quote(self.product)
        sale_line = quote.order_line
        self.assertEqual(sale_line.price_unit, 100.0)
        ticket = self._charge([self._pos_line(
            sale_line, 80.0, 5.0, price_origin='pricelist', price_origin_label='Tarifa QA',
            pricing_rule_type='manual', pricing_rule_origin='Manual 5%',
        )])
        self.assertEqual(ticket.state, 'paid')
        self.assertEqual(quote.state, 'sale')
        self.assertEqual(sale_line.price_unit, 80.0)
        self.assertEqual(sale_line.discount, 5.0)
        self.assertEqual(sale_line.price_origin, 'pricelist')
        self.assertEqual(sale_line.pricing_rule_type, 'manual')
        self.assertAlmostEqual(quote.amount_total, ticket.amount_total)
        self.assertEqual(quote.amount_unpaid, 0.0)
        self.assertTrue(quote.message_ids.filtered(
            lambda message: 'Precios actualizados con lo cobrado en el ticket' in message.body))

    def test_confirmed_quotation_without_invoice_takes_charged_prices(self):
        quote = self._quote(self.product)
        quote.action_confirm()
        self.assertEqual(quote.state, 'sale')
        ticket = self._charge([self._pos_line(quote.order_line, 90.0)])
        self.assertEqual(quote.order_line.price_unit, 90.0)
        self.assertAlmostEqual(quote.amount_total, ticket.amount_total)
        self.assertEqual(quote.amount_unpaid, 0.0)

    def test_tinted_quotation_line_takes_charged_price_and_snapshot(self):
        quote = self._quote()
        snapshot = {'name': 'Rojo QA', 'base_id': self.product.id, 'base_only': True, 'unit_price': 200.0,
                    'currency_id': quote.currency_id.id, 'color_code': 'QA-ROJO', 'doses': []}
        sale_line = self.env['sale.order.line'].with_context(_tint_operation=_TINT_OPERATION).create({
            'order_id': quote.id, 'product_id': self.product.id, 'product_uom_qty': 1, 'price_unit': 200.0,
            'technical_price_unit': 200.0, 'tint_snapshot': snapshot, 'tint_container_qty': 1,
            'price_origin': 'tint',
        })
        ticket = self._charge([self._pos_line(sale_line, 224.0, price_origin='tint')])
        self.assertEqual(sale_line.price_unit, 224.0)
        self.assertEqual(sale_line.tint_snapshot['unit_price'], 224.0)
        self.assertEqual(sale_line.tint_snapshot['color_code'], 'QA-ROJO')
        self.assertAlmostEqual(quote.amount_total, ticket.amount_total)
        self.assertEqual(quote.amount_unpaid, 0.0)

    def test_line_promotion_is_written_to_the_quotation(self):
        program = self.env['loyalty.program'].create({
            'name': 'Promoción línea traer cotización QA', 'program_type': 'promotion',
            'trigger': 'auto', 'applies_on': 'current',
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': 'order', 'reward_point_amount': 1,
            })],
            'reward_ids': [Command.clear(), Command.create({
                'reward_type': 'discount', 'discount': 15.0, 'discount_mode': 'percent',
                'discount_applicability': 'specific',
                'discount_product_ids': [Command.set(self.product.ids)],
            })],
        })
        reward = program.reward_ids
        quote = self._quote(self.product)
        ticket = self._charge([self._pos_line(
            quote.order_line, 100.0, 15.0, pricing_rule_type='promo', promo_reward_id=reward.id,
            pricing_rule_origin='Promoción línea traer cotización QA 15%',
        )])
        self.assertEqual(quote.order_line.promo_reward_id, reward)
        self.assertEqual(quote.order_line.pricing_rule_type, 'promo')
        self.assertEqual(quote.order_line.discount, 15.0)
        self.assertAlmostEqual(quote.amount_total, ticket.amount_total)
        self.assertEqual(quote.amount_unpaid, 0.0)

    def test_draft_ticket_does_not_change_the_quotation(self):
        quote = self._quote(self.product)
        self._charge([self._pos_line(quote.order_line, 80.0)], state='draft')
        self.assertEqual(quote.state, 'draft')
        self.assertEqual(quote.order_line.price_unit, 100.0)

    def test_split_lines_with_different_prices_leave_the_sale_line(self):
        quote = self._quote(self.product)
        sale_line = quote.order_line
        self._charge([self._pos_line(sale_line, 80.0, qty=1), self._pos_line(sale_line, 90.0, qty=1)])
        self.assertEqual(sale_line.price_unit, 100.0)
        self.assertTrue(quote.message_ids.filtered(lambda message: 'No se actualizaron' in message.body))
