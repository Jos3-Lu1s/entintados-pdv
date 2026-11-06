# -*- coding: utf-8 -*-

from lxml import etree

from odoo import Command
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaleGiftPromotion(TransactionCase):
    """El producto gratis de una promoción es la línea de recompensa nativa."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
        )
        cls.env['loyalty.program'].search([]).active = False
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Regalo',
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.product_p = cls._create_product('P Regalo', 100.0)
        cls.product_q = cls._create_product('Q Regalo', 50.0)
        cls.partner_c = cls._create_partner('Cliente C Regalo')
        cls.partner_c2 = cls._create_partner('Cliente C2 Regalo')
        cls._create_rule(cls.partner_c2, cls.product_p, discount=10.0)
        cls._create_rule(cls.partner_c2, cls.product_q, fixed_price=70.0)
        cls.program = cls.env['loyalty.program'].create({
            'name': 'Lleva2',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [Command.clear(), Command.create({
                'product_ids': [Command.set(cls.product_p.ids)],
                'minimum_qty': 2,
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
            })],
            'reward_ids': [Command.clear(), Command.create({
                'reward_type': 'product',
                'reward_product_id': cls.product_q.id,
                'reward_product_qty': 1,
                'required_points': 1,
            })],
        })

    @classmethod
    def _create_product(cls, name, price, **values):
        return cls.env['product.product'].create({
            'name': name,
            'list_price': price,
            'invoice_policy': 'order',
            'taxes_id': [Command.clear()],
            **values,
        })

    @classmethod
    def _create_partner(cls, name):
        partner = cls.env['res.partner'].create({
            'name': name,
            'is_customer': True,
            'phone': '1234567890',
        })
        partner.property_product_pricelist = cls.pricelist
        return partner

    @classmethod
    def _create_rule(cls, partner, product, discount=0.0, fixed_price=0.0):
        return cls.env['res.partner.discount.rule'].create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price' if fixed_price else 'discount',
            'product_id': product.id,
            'discount': discount,
            'fixed_price': fixed_price,
        })

    def _create_order(self, partner=None):
        return self.env['sale.order'].create({
            'partner_id': (partner or self.partner_c).id,
            'pricelist_id': self.pricelist.id,
        })

    def _add_line(self, order, product, qty=1.0):
        return self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': product.id,
            'product_uom_qty': qty,
        })

    def _claim_gift(self, order, program=None):
        program = program or self.program
        order._update_programs_and_rewards()
        coupon = order.coupon_point_ids.coupon_id.filtered(lambda card: card.program_id == program)
        result = order._apply_program_reward(program.reward_ids, coupon)
        self.assertNotIn('error', result)

    def _order_with_gift(self, partner=None):
        order = self._create_order(partner)
        self._add_line(order, self.product_p, 2.0)
        self._claim_gift(order)
        return order

    def _gift_line(self, order):
        return order.order_line.filtered(
            lambda line: line.product_id == self.product_q and line.discount == 100.0
        )

    def test_gift_is_native_reward_line(self):
        order = self._order_with_gift()
        gift = order.order_line.filtered(lambda line: line.product_id == self.product_q)
        self.assertEqual(len(gift), 1)
        self.assertEqual(gift.reward_id, self.program.reward_ids)
        self.assertTrue(gift.is_reward_line)
        self.assertEqual(gift.product_uom_qty, 1.0)
        self.assertEqual(gift.discount, 100.0)
        self.assertAlmostEqual(order.amount_total, 200.0)

    def test_gift_badge(self):
        gift = self._gift_line(self._order_with_gift())
        self.assertEqual(gift.pricing_rule_type, 'promo')
        self.assertEqual(gift.pricing_rule_origin, 'Promoción: Lleva2')
        self.assertFalse(gift.pricing_rule_id)
        self.assertFalse(gift.promo_reward_id)

    def test_partner_change_keeps_gift(self):
        order = self._order_with_gift()
        with Form(order) as form:
            form.partner_id = self.partner_c2
        gift = order.order_line.filtered(lambda line: line.product_id == self.product_q)
        self.assertEqual(gift.discount, 100.0)
        self.assertAlmostEqual(gift.price_unit, 50.0)
        self.assertEqual(gift.pricing_rule_origin, 'Promoción: Lleva2')
        self.assertAlmostEqual(order.amount_total, 180.0)

    def test_update_prices_keeps_gift(self):
        order = self._order_with_gift()
        order.action_update_prices()
        gift = order.order_line.filtered(lambda line: line.product_id == self.product_q)
        self.assertEqual(gift.discount, 100.0)
        self.assertEqual(gift.pricing_rule_origin, 'Promoción: Lleva2')
        self.assertAlmostEqual(order.amount_total, 200.0)

    def test_pricelist_change_keeps_gift(self):
        discounted = self.env['product.pricelist'].create({
            'name': 'Tarifa Regalo -10',
            'currency_id': self.env.company.currency_id.id,
            'item_ids': [Command.create({
                'applied_on': '3_global',
                'compute_price': 'percentage',
                'percent_price': 10.0,
            })],
        })
        order = self._order_with_gift()
        order.pricelist_id = discounted
        order.action_update_prices()
        gift = order.order_line.filtered(lambda line: line.product_id == self.product_q)
        self.assertEqual(gift.discount, 100.0)
        self.assertEqual(gift.pricing_rule_origin, 'Promoción: Lleva2')
        self.assertAlmostEqual(order.amount_total, 180.0)

    def test_gift_beside_paid_line(self):
        order = self._create_order()
        self._add_line(order, self.product_p, 2.0)
        paid = self._add_line(order, self.product_q, 3.0)
        self._claim_gift(order)
        gift = order.order_line.filtered('reward_id')
        self.assertEqual(gift.product_id, self.product_q)
        self.assertEqual(gift.product_uom_qty, 1.0)
        self.assertEqual(gift.discount, 100.0)
        self.assertEqual(paid.product_uom_qty, 3.0)
        self.assertEqual(paid.discount, 0.0)
        self.assertAlmostEqual(order.amount_total, 350.0)

    def test_paid_line_beside_gift(self):
        order = self._order_with_gift()
        self._add_line(order, self.product_q)
        self.assertEqual(len(order.order_line.filtered(lambda line: line.product_id == self.product_q)), 2)
        self.assertAlmostEqual(order.amount_total, 250.0)

    def test_gift_equal_to_tinted_base(self):
        base_type = self.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        size = self.env['tint.size'].search([('code', '=', 'G')], limit=1)
        base = self._create_product(
            'Base Regalo', 200.0, default_code='TEST-GIFT-BASE', tint_role='base',
            tint_base_type_id=base_type.id, tint_size_id=size.id, is_storable=True,
        )
        colorant = self._create_product(
            'Colorante Regalo', 2.0, default_code='TEST-GIFT-RED', tint_role='colorant',
            uom_id=self.env.ref('entintados_pdv.uom_tint_point').id, is_storable=True,
        )
        color = self.env['tint.color'].create({
            'name': 'Color Regalo',
            'gallery_id': self.env['tint.gallery'].search([], limit=1).id,
            'code_number': '9874',
        })
        formula = self.env['tint.color.formula'].create({
            'color_id': color.id,
            'base_type_id': base_type.id,
            'size_id': size.id,
            'line_ids': [Command.create({'colorant_id': colorant.id, 'points': 12})],
        })
        self.program.reward_ids.reward_product_id = base
        order = self._create_order()
        tint_line = order._apply_tint(formula, base, 1)
        self._add_line(order, self.product_p, 2.0)
        tint_values = (tint_line.product_uom_qty, tint_line.price_unit, tint_line.discount)

        self._claim_gift(order)

        gift = order.order_line.filtered('reward_id')
        self.assertEqual(gift.product_id, base)
        self.assertEqual(gift.discount, 100.0)
        self.assertNotEqual(gift, tint_line)
        self.assertEqual((tint_line.product_uom_qty, tint_line.price_unit, tint_line.discount), tint_values)

    def _lower_p_to_one(self, order):
        index = order.order_line.ids.index(
            order.order_line.filtered(lambda line: line.product_id == self.product_p).id
        )
        with Form(order) as form:
            with form.order_line.edit(index) as line:
                line.product_uom_qty = 1.0

    def test_gift_removed_when_condition_lost(self):
        order = self._order_with_gift()
        self._lower_p_to_one(order)
        self.assertFalse(order.order_line.filtered(lambda line: line.product_id == self.product_q))
        self.assertAlmostEqual(order.amount_total, 100.0)

    def test_gift_removed_notice(self):
        order = self._order_with_gift()
        self._lower_p_to_one(order)
        self.assertIn(
            "Se retiró el regalo Q Regalo de la promoción Lleva2: ya no se cumple la condición.",
            order.message_ids[:1].body,
        )

    def test_gift_kept_on_confirm(self):
        order = self._order_with_gift()
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        gift = self._gift_line(order)
        self.assertEqual(gift.reward_id, self.program.reward_ids)
        self.assertAlmostEqual(gift.price_unit, 50.0)
        self.assertAlmostEqual(order.amount_total, 200.0)

    def _order_line_views(self):
        arch = etree.fromstring(self.env['sale.order'].get_views([(False, 'form')])['views']['form']['arch'])
        order_line = arch.xpath("//field[@name='order_line']")[0]
        return {view: order_line.find(view) for view in ('list', 'form', 'kanban')}

    @staticmethod
    def _field(container, name):
        return container.xpath(f".//field[@name='{name}']")[0]

    def test_reward_line_price_readonly(self):
        views = self._order_line_views()
        for view in ('list', 'form'):
            with self.subTest(view=view):
                self.assertIn('is_reward_line', self._field(views[view], 'price_unit').get('readonly'))
        order = self._order_with_gift()
        index = order.order_line.ids.index(self._gift_line(order).id)
        with Form(order) as form:
            with form.order_line.edit(index) as line:
                with self.assertRaises(AssertionError):
                    line.price_unit = 10.0

    def test_gift_qty_readonly(self):
        views = self._order_line_views()
        for view in ('list', 'form'):
            with self.subTest(view=view):
                self.assertIn('is_reward_line', self._field(views[view], 'product_uom_qty').get('readonly'))
        self.assertTrue(views['form'].xpath(".//field[@name='is_reward_line']"))
        self.assertTrue(views['kanban'].xpath(".//field[@name='is_reward_line']"))
        order = self._order_with_gift()
        index = order.order_line.ids.index(self._gift_line(order).id)
        with Form(order) as form:
            with form.order_line.edit(index) as line:
                with self.assertRaises(AssertionError):
                    line.product_uom_qty = 2.0

    def test_gift_price_is_not_manual(self):
        order = self._order_with_gift()
        gift = self._gift_line(order)
        self.assertEqual(gift.price_origin, 'list')
        order._update_programs_and_rewards()
        self.assertEqual(gift.price_origin, 'list')
        order.action_confirm()
        self.assertEqual(gift.price_origin, 'list')
        self.assertAlmostEqual(gift.price_unit, 50.0)
