# -*- coding: utf-8 -*-

from odoo import Command
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestServiceAgreements(TransactionCase):
    """Los servicios reciben los acuerdos comerciales del cliente igual que los bienes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rules = cls.env['res.partner.discount.rule']
        cls.service = cls.env['product.product'].create({
            'name': 'Igualación de Color',
            'type': 'service',
            'list_price': 250.0,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Servicios',
            'is_customer': True,
            'phone': '1234567890',
        })

    def _create_order(self, product, partner=None, qty=1.0):
        with Form(self.env['sale.order']) as order_form:
            order_form.partner_id = partner or self.partner
            with order_form.order_line.new() as line_form:
                line_form.product_id = product
                line_form.product_uom_qty = qty
        return order_form.record

    def test_service_fixed_price_keeps_badge(self):
        rule = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.service.id,
            'fixed_price': 80.0,
        })

        order = self._create_order(self.service)
        line = order.order_line
        self._assert_fixed_price(line, rule, 80.0)

        order.action_update_prices()
        self._assert_fixed_price(line, rule, 80.0)

    def _assert_fixed_price(self, line, rule, price):
        self.assertEqual(line.price_unit, price)
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(line.pricing_rule_type, 'fixed_price')
        self.assertEqual(line.pricing_rule_id, rule)
        self.assertEqual(line.price_origin, 'fixed_price')

    def _fixed_price_rule(self, product, price):
        return self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': product.id,
            'fixed_price': price,
        })

    def _goods_line(self):
        goods = self.env['product.product'].create({'name': 'Brocha 4 Pulgadas', 'list_price': 100.0})
        return goods, self._create_order(goods).order_line

    def _assert_list_price(self, line, price):
        self.assertEqual(line.price_unit, price)
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(line.pricing_rule_type, 'none')
        self.assertNotIn(line.price_origin, ('fixed_price', 'manual'))

    def test_write_product_to_service_drops_previous_fixed_price(self):
        goods, line = self._goods_line()
        self._fixed_price_rule(goods, 70.0)
        line.order_id.action_update_prices()
        self.assertEqual(line.pricing_rule_type, 'fixed_price')

        line.write({'product_id': self.service.id})

        self._assert_list_price(line, 250.0)

    def test_write_product_to_service_drops_previous_manual_price(self):
        goods, line = self._goods_line()
        line.write({'price_unit': 90.0})
        self.assertEqual(line.price_origin, 'manual')

        line.write({'product_id': self.service.id})

        self._assert_list_price(line, 250.0)

    def test_write_product_to_service_applies_its_fixed_price(self):
        goods, line = self._goods_line()
        rule = self._fixed_price_rule(self.service, 80.0)

        line.write({'product_id': self.service.id})

        self._assert_fixed_price(line, rule, 80.0)

    def _discount_rule(self, applied_on, target, discount):
        target_field = {'0_product': 'product_id', '1_line': 'line_id', '2_scheme': 'scheme_id'}[applied_on]
        return self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': applied_on,
            'rule_type': 'discount',
            target_field: target.id,
            'discount': discount,
        })

    def _service_in_line(self):
        scheme = self.env['tint.schema'].create({'name': 'Esquema Servicios'})
        product_line = self.env['lines.product'].create({'name': 'Línea Servicios', 'scheme': scheme.id})
        self.service.lines_product_id = product_line
        return product_line, scheme

    def _assert_discount(self, line, discount, rule_type, label, rule=None):
        self.assertEqual(line.price_unit, 250.0)
        self.assertEqual(line.discount, discount)
        self.assertEqual(line.pricing_rule_type, rule_type)
        self.assertEqual(line.pricing_rule_origin, label)
        self.assertEqual(line.pricing_rule_id, rule or self.rules)

    def test_service_product_discount(self):
        rule = self._discount_rule('0_product', self.service, 10.0)

        line = self._create_order(self.service).order_line

        self._assert_discount(line, 10.0, 'product', 'Desc. Producto (10.0%)', rule)

    def test_service_line_discount(self):
        product_line, _scheme = self._service_in_line()
        rule = self._discount_rule('1_line', product_line, 12.0)

        line = self._create_order(self.service).order_line

        self._assert_discount(line, 12.0, 'line', 'Desc. Línea: Línea Servicios (12.0%)', rule)

    def test_service_scheme_discount(self):
        _product_line, scheme = self._service_in_line()
        rule = self._discount_rule('2_scheme', scheme, 15.0)

        line = self._create_order(self.service).order_line

        self._assert_discount(line, 15.0, 'scheme', 'Desc. Esquema: Esquema Servicios (15.0%)', rule)

    def test_service_partner_global_discount(self):
        self.partner.discount = 0.10

        line = self._create_order(self.service).order_line

        self._assert_discount(line, 10.0, 'global', 'Desc. Global Cliente (10.0%)')

    def test_service_fixed_price_survives_recomputes(self):
        rule = self._fixed_price_rule(self.service, 80.0)
        order = self._create_order(self.service)
        line = order.order_line

        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                line_form.product_uom_qty = 3
        self.assertEqual(line.product_uom_qty, 3)
        self._assert_fixed_price(line, rule, 80.0)

        pricelist = self.env['product.pricelist'].create({'name': 'Tarifa Servicios'})
        with Form(order) as order_form:
            order_form.pricelist_id = pricelist
        self.assertEqual(order.pricelist_id, pricelist)
        self._assert_fixed_price(line, rule, 80.0)

        order.action_update_prices()
        self._assert_fixed_price(line, rule, 80.0)

    def test_service_discount_survives_update_prices(self):
        rule = self._discount_rule('0_product', self.service, 10.0)
        order = self._create_order(self.service)

        order.action_update_prices()

        self._assert_discount(order.order_line, 10.0, 'product', 'Desc. Producto (10.0%)', rule)

    def _assert_without_agreement(self, line):
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(line.pricing_rule_type, 'none')
        self.assertFalse(line.pricing_rule_id)

    def _goods_order_with_global_discount(self):
        self.partner.discount = 0.10
        goods, line = self._goods_line()
        return line.order_id

    def test_downpayment_line_without_agreement(self):
        order = self._goods_order_with_global_discount()
        order.action_confirm()
        self.env['sale.advance.payment.inv'].create({
            'sale_order_ids': [Command.set(order.ids)],
            'advance_payment_method': 'fixed',
            'fixed_amount': 50.0,
        }).create_invoices()
        down_payment = order.order_line.filtered(lambda line: line.is_downpayment and not line.display_type)
        self.assertTrue(down_payment)

        order.action_update_prices()

        self._assert_without_agreement(down_payment)

    def test_global_discount_line_without_agreement(self):
        order = self._goods_order_with_global_discount()
        wizard = self.env['sale.order.discount'].create({
            'sale_order_id': order.id,
            'discount_type': 'so_discount',
            'discount_percentage': 0.05,
        })
        self._discount_rule('0_product', wizard._get_discount_product(), 20.0)
        wizard._create_discount_lines()
        discount_line = order.order_line.filtered(lambda line: line._is_global_discount())
        self.assertTrue(discount_line)
        price = discount_line.price_unit

        order.action_update_prices()

        self._assert_without_agreement(discount_line)
        self.assertEqual(discount_line.price_unit, price)

    def test_reward_line_without_agreement(self):
        order = self._goods_order_with_global_discount()
        program = self.env['loyalty.program'].create({
            'name': 'Promo $20',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {'reward_point_mode': 'order', 'reward_point_amount': 1})],
            # Las recompensas de porcentaje no crean línea: descuentan las del pedido.
            'reward_ids': [(0, 0, {
                'required_points': 1,
                'reward_type': 'discount',
                'discount': 20.0,
                'discount_mode': 'per_order',
                'discount_applicability': 'order',
            })],
        })
        reward = program.reward_ids
        self._discount_rule('0_product', reward.discount_line_product_id, 20.0)
        order._update_programs_and_rewards()
        coupon = next(coupon for coupon in order._get_claimable_rewards() if coupon.program_id == program)
        self.assertFalse(order._apply_program_reward(reward, coupon))
        reward_line = order.order_line.filtered('is_reward_line')
        self.assertTrue(reward_line)

        order.action_update_prices()

        self._assert_without_agreement(reward_line)
