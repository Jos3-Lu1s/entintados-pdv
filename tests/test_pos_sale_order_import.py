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

    def _combo_quote(self):
        """Cotización con un paquete de combo y su producto, ligados como los deja Ventas."""
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
        item_line = self.env['sale.order.line'].create({
            'order_id': quote.id, 'product_id': self.product.id, 'product_uom_qty': 1,
            'combo_item_id': combo.combo_item_ids.id, 'linked_line_id': package_line.id,
        })
        return quote, package_line, item_line

    def test_quotation_with_combo_loads(self):
        quote, package_line, item_line = self._combo_quote()
        result = quote.load_sale_order_from_pos(self.config.id)
        loaded = {line['id']: line for line in result['sale.order.line']}
        self.assertLessEqual({package_line.id, item_line.id}, set(loaded))
        self.assertEqual(loaded[item_line.id]['linked_line_id'], package_line.id)
        self.assertEqual(loaded[item_line.id]['combo_item_id'], item_line.combo_item_id.id)
        self.assertIn('product_no_variant_attribute_value_ids', loaded[item_line.id])

    def test_combo_with_changed_configuration_is_rejected(self):
        quote, package_line, _item_line = self._combo_quote()
        other = self.env['product.combo'].create({
            'name': 'Otro combo traer cotización QA',
            'combo_item_ids': [Command.create({'product_id': self.gift.id})],
        })
        package_line.product_id.combo_ids = other
        with self.assertRaisesRegex(UserError, 'cambió su configuración'):
            quote.load_sale_order_from_pos(self.config.id)

    def test_combo_with_archived_product_is_rejected(self):
        quote, _package_line, item_line = self._combo_quote()
        item_line.product_id.active = False
        with self.assertRaisesRegex(UserError, 'tiene productos archivados'):
            quote.load_sale_order_from_pos(self.config.id)

    def test_combo_with_delivered_quantity_is_rejected(self):
        quote, _package_line, item_line = self._combo_quote()
        item_line.qty_delivered = 1
        with self.assertRaisesRegex(UserError, 'ya tiene entregas o facturas'):
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
        with self.assertRaisesRegex(UserError, 'trae regalos de promoción'):
            quote.load_sale_order_from_pos(self.config.id)

    def _tinted_quote(self, colorant):
        quote = self._quote()
        snapshot = {'name': 'Azul QA', 'base_id': self.product.id, 'base_only': True, 'unit_price': 150.0,
                    'currency_id': quote.currency_id.id, 'color_code': 'QA-AZUL',
                    'doses': [{'product_id': colorant.id, 'points': 4, 'name': colorant.display_name,
                               'unit_price': 2.0}]}
        self.env['sale.order.line'].with_context(_tint_operation=_TINT_OPERATION).create({
            'order_id': quote.id, 'product_id': self.product.id, 'product_uom_qty': 1, 'price_unit': 150.0,
            'technical_price_unit': 150.0, 'tint_snapshot': snapshot, 'tint_container_qty': 1,
            'price_origin': 'tint',
        })
        return quote

    def test_tint_colorants_are_loaded_with_the_quotation(self):
        colorant = self._product('Colorante traer cotización QA', 2.0, available_in_pos=False)
        result = self._tinted_quote(colorant).load_sale_order_from_pos(self.config.id)
        product_ids = [product['id'] for product in result['product.product']]
        self.assertIn(colorant.id, product_ids)
        self.assertIn(self.product.id, product_ids)
        self.assertEqual(len(product_ids), len(set(product_ids)))

    def test_archived_tint_colorant_rejects_the_quotation(self):
        colorant = self._product('Colorante archivado traer cotización QA', 2.0)
        quote = self._tinted_quote(colorant)
        colorant.active = False
        with self.assertRaisesRegex(UserError, 'usa el colorante archivado Colorante archivado traer cotización QA'):
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

    def _charge(self, lines, state='paid', mapping=None):
        total = sum(line[2]['price_subtotal_incl'] for line in lines)
        paid = state == 'paid'
        order = {
            'uuid': str(uuid4()), 'session_id': self.session.id, 'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id, 'state': state, 'lines': lines,
            'amount_tax': 0.0, 'amount_total': total, 'amount_return': 0.0,
            'amount_paid': total if paid else 0.0,
            'payment_ids': [(0, 0, {'payment_method_id': self.cash.id, 'amount': total})] if paid else [],
        }
        if mapping:
            order['relations_uuid_mapping'] = {'pos.order.line': mapping}
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

    def test_combo_quotation_takes_charged_prices(self):
        quote, package_line, item_line = self._combo_quote()
        regular_line = quote.order_line - package_line - item_line
        agreement = {'discount': 5.0, 'pricing_rule_type': 'manual'}
        package = self._pos_line(package_line, 0.0, pricing_rule_origin='Manual 5%', price_origin='list', **agreement)
        item = self._pos_line(item_line, 280.0, pricing_rule_origin='↳ Manual 5%', price_origin='combo',
                              combo_item_id=item_line.combo_item_id.id, **agreement)
        ticket = self._charge(
            [package, item, self._pos_line(regular_line, 100.0)],
            mapping={item[2]['uuid']: {'combo_parent_id': package[2]['uuid']}},
        )
        self.assertEqual(ticket.lines.filtered('combo_item_id').combo_parent_id.sale_order_line_id, package_line)
        self.assertEqual(package_line.price_unit, 0.0)
        self.assertEqual((item_line.price_unit, item_line.discount, item_line.pricing_rule_type),
                         (280.0, 5.0, 'manual'))
        self.assertEqual(item_line.price_origin, 'combo')
        self.assertAlmostEqual(quote.amount_total, ticket.amount_total)
        self.assertEqual(quote.amount_unpaid, 0.0)
        self.assertTrue(quote.message_ids.filtered(
            lambda message: 'Precios actualizados con lo cobrado en el ticket' in message.body))

    # --- Hijas de colorante de un entintado traído -------------------------

    def _tinted_ticket(self):
        """Ticket cobrado con una base traída de Ventas y sus 2 hijas de colorante a $0."""
        point = self.env.ref('entintados_pdv.uom_tint_point')
        base = self._product('Base traer cotización QA', 150.0, tint_role='base', is_storable=True,
                             tint_base_type_id=self.env['tint.base.type'].search([('code', '=', 'W')], limit=1).id,
                             tint_size_id=self.env['tint.size'].search([], limit=1).id)
        colorants = [self._product(name, 2.0, tint_role='colorant', is_storable=True, uom_id=point.id)
                     for name in ('Colorante A traer cotización QA', 'Colorante B traer cotización QA')]
        location = self.config.picking_type_id.default_location_src_id
        quant = self.env['stock.quant']
        quant._update_available_quantity(base, location, 2)
        for colorant in colorants:
            quant._update_available_quantity(colorant, location, 20)
        quote = self._quote()
        snapshot = {'name': 'Verde QA', 'base_id': base.id, 'base_only': True, 'unit_price': 164.0,
                    'currency_id': quote.currency_id.id, 'color_code': 'QA-VERDE',
                    'doses': [{'product_id': colorants[0].id, 'points': 4, 'name': colorants[0].name},
                              {'product_id': colorants[1].id, 'points': 3, 'name': colorants[1].name}]}
        sale_line = self.env['sale.order.line'].with_context(_tint_operation=_TINT_OPERATION).create({
            'order_id': quote.id, 'product_id': base.id, 'product_uom_qty': 2, 'price_unit': 164.0,
            'technical_price_unit': 164.0, 'tint_snapshot': snapshot, 'tint_container_qty': 2,
            'price_origin': 'tint',
        })
        # Only the ticket's own stock is under test, not the quotation confirmation.
        ticket = self.env['pos.order'].create({
            'session_id': self.session.id, 'partner_id': self.partner.id, 'pricelist_id': self.pricelist.id,
            'amount_total': 328.0, 'amount_tax': 0, 'amount_paid': 328.0, 'amount_return': 0,
            'lines': [Command.create({
                'product_id': base.id, 'qty': 2, 'price_unit': 164.0, 'price_subtotal': 328.0,
                'price_subtotal_incl': 328.0, 'tint_color_code': 'QA-VERDE', 'tax_ids': [Command.clear()],
                'sale_order_line_id': sale_line.id, 'sale_order_origin_id': quote.id,
            })],
        })
        for colorant, points in zip(colorants, (4, 3)):
            self.env['pos.order.line'].create({
                'order_id': ticket.id, 'product_id': colorant.id, 'qty': points * 2, 'price_unit': 0,
                'price_subtotal': 0, 'price_subtotal_incl': 0, 'combo_parent_id': ticket.lines[0].id,
                'tax_ids': [Command.clear()],
            })
        ticket.state = 'paid'
        return ticket, base, colorants, location

    def _available(self, product, location):
        return self.env['stock.quant']._get_available_quantity(product, location)

    def test_imported_tint_colorant_children_do_not_move_stock(self):
        ticket, base, colorants, location = self._tinted_ticket()
        base_line = ticket.lines.filtered(lambda line: not line.combo_parent_id)
        self.assertFalse(base_line._is_imported_tint_colorant())
        self.assertTrue(all(child._is_imported_tint_colorant() for child in base_line.combo_line_ids))
        ticket._create_order_picking()
        picking = ticket.picking_ids
        consumption = picking._tint_consumption_moves()
        self.assertEqual((picking.move_ids - consumption).product_id, base)
        self.assertEqual({move.product_id: move.quantity for move in consumption},
                         {colorants[0]: 8, colorants[1]: 6})
        self.assertEqual(self.env['stock.move'].search([('product_id', 'in', [c.id for c in colorants])]),
                         consumption)
        self.assertEqual(self._available(colorants[0], location), 12)
        self.assertEqual(self._available(colorants[1], location), 14)

    def test_refund_of_imported_tint_does_not_move_colorant_children(self):
        ticket, base, colorants, location = self._tinted_ticket()
        ticket._create_order_picking()
        base_line = ticket.lines.filtered(lambda line: not line.combo_parent_id)
        refund = self.env['pos.order'].create({
            'session_id': self.session.id, 'partner_id': self.partner.id, 'pricelist_id': self.pricelist.id,
            'amount_total': -328.0, 'amount_tax': 0, 'amount_paid': 0, 'amount_return': 0,
            'lines': [Command.create({
                'product_id': base.id, 'qty': -2, 'price_unit': 164.0, 'price_subtotal': -328.0,
                'price_subtotal_incl': -328.0, 'refunded_orderline_id': base_line.id, 'tax_ids': [Command.clear()],
            })],
        })
        for child in base_line.combo_line_ids:
            self.env['pos.order.line'].create({
                'order_id': refund.id, 'product_id': child.product_id.id, 'qty': -child.qty, 'price_unit': 0,
                'price_subtotal': 0, 'price_subtotal_incl': 0, 'combo_parent_id': refund.lines[0].id,
                'refunded_orderline_id': child.id, 'tax_ids': [Command.clear()],
            })
        refund_children = refund.lines.filtered('combo_parent_id')
        self.assertEqual(len(refund_children), 2)
        self.assertTrue(all(child._is_imported_tint_colorant() for child in refund_children))
        refund.state = 'paid'
        refund._create_order_picking()
        self.assertEqual(refund.picking_ids.move_ids.product_id, base)
        self.assertEqual(self._available(colorants[0], location), 12)
        self.assertEqual(self._available(colorants[1], location), 14)
