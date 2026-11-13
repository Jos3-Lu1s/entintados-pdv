# -*- coding: utf-8 -*-

from lxml import etree

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestSaleLinePromotion(TransactionCase):
    """Cada línea elige entre su acuerdo comercial y una promoción de %."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
            | cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
            | cls.env.ref('entintados_pdv.group_sale_edit_discount')
        )
        cls.env['loyalty.program'].search([]).active = False
        cls.lines = cls.env['sale.order.line']
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Promoción por Línea',
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Promoción por Línea',
            'is_customer': True,
            'phone': '1234567890',
        })
        cls.partner.property_product_pricelist = cls.pricelist
        cls.product_agreement = cls._create_product('Vinílica Promo', 100.0)
        cls.product_plain = cls._create_product('Brocha Promo', 50.0)
        cls._create_rule(cls.product_agreement, discount=10.0)

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
    def _create_rule(cls, product, discount=0.0, fixed_price=0.0):
        return cls.env['res.partner.discount.rule'].create({
            'partner_id': cls.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price' if fixed_price else 'discount',
            'product_id': product.id,
            'discount': discount,
            'fixed_price': fixed_price,
        })

    @classmethod
    def _create_program(cls, rewards, program_type='promotion', name='Promo Línea', **rule):
        return cls.env['loyalty.program'].create({
            'name': name,
            'program_type': program_type,
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
                **rule,
            })],
            'reward_ids': [Command.clear()] + [Command.create({
                'reward_type': 'discount',
                'discount_mode': 'percent',
                'discount_applicability': 'order',
                'required_points': 1,
                **reward,
            }) for reward in rewards],
        })

    def _create_order(self):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
        })

    def _create_line(self, order, product=None, qty=1.0):
        return self.lines.create({
            'order_id': order.id,
            'product_id': (product or self.product_agreement).id,
            'product_uom_qty': qty,
        })

    def test_reward_scope(self):
        program = self._create_program([
            {'discount': 20.0},
            {'discount': 15.0, 'discount_applicability': 'specific',
             'discount_product_ids': [Command.set(self.product_plain.ids)]},
            {'discount': 5.0, 'discount_applicability': 'cheapest'},
            {'discount': 7.0, 'discount_max_amount': 30.0},
            {'discount': 50.0, 'discount_mode': 'per_order'},
            {'reward_type': 'product', 'reward_product_id': self.product_plain.id},
        ])
        in_scope = program.reward_ids.filtered(lambda reward: reward._is_line_promotion())
        self.assertEqual(sorted(in_scope.mapped('discount')), [15.0, 20.0])

        for program_type, expected in (('promo_code', False), ('coupons', False), ('loyalty', False)):
            other = self._create_program([{'discount': 20.0}], program_type=program_type)
            self.assertEqual(other.reward_ids._is_line_promotion(), expected, program_type)

    def test_usage_counts_once_per_order(self):
        program = self._create_program([{'discount': 10.0}, {'discount': 20.0}])
        first_reward, second_reward = program.reward_ids
        other_program = self._create_program([{'discount': 30.0}], name='Otra Promo')

        order = self._create_order()
        self._create_line(order).promo_reward_id = first_reward
        self._create_line(order, self.product_plain).promo_reward_id = second_reward
        self.env.flush_all()
        program.invalidate_recordset(['order_count', 'total_order_count'])
        self.assertEqual(program.order_count, 1)
        self.assertEqual(program.total_order_count, 1)
        self.assertEqual(other_program.order_count, 0)

        self._create_line(self._create_order()).promo_reward_id = second_reward
        self.env.flush_all()
        program.invalidate_recordset(['order_count', 'total_order_count'])
        self.assertEqual(program.order_count, 2)

    def test_usage_counts_native_and_promo_lines_once(self):
        program = self._create_program([{'discount': 10.0}, {'discount': 20.0, 'discount_mode': 'per_order'}])
        line_reward = program.reward_ids.filtered(lambda reward: reward.discount == 10.0)
        native_reward = program.reward_ids - line_reward
        order = self._create_order()
        self._create_line(order).promo_reward_id = line_reward
        coupon = self.env['loyalty.card'].create({'program_id': program.id, 'points': 1})
        self.lines.create({
            'order_id': order.id,
            'name': native_reward.description,
            'product_id': native_reward.discount_line_product_id.id,
            'reward_id': native_reward.id,
            'coupon_id': coupon.id,
            'product_uom_qty': 1.0,
            'price_unit': -20.0,
        })
        self.env.flush_all()
        program.invalidate_recordset(['order_count'])
        self.assertEqual(program.order_count, 1)

    def test_line_eligibility(self):
        order = self._create_order()
        self.assertTrue(self._create_line(order)._admits_line_promotion())
        service = self._create_product('Servicio Promo', 30.0, type='service')
        self.assertTrue(self._create_line(order, service)._admits_line_promotion())

        fixed_product = self._create_product('Precio Fijo Promo', 80.0)
        self._create_rule(fixed_product, fixed_price=70.0)
        self.assertFalse(self._create_line(order, fixed_product)._admits_line_promotion())

        section = self.lines.create({'order_id': order.id, 'display_type': 'line_section', 'name': 'Sección'})
        self.assertFalse(section._admits_line_promotion())

        item_product = self._create_product('Producto Combo Promo', 40.0)
        combo = self.env['product.combo'].create({
            'name': 'Combo Promo',
            'combo_item_ids': [Command.create({'product_id': item_product.id})],
        })
        package = self._create_product('Paquete Promo', 40.0, type='combo', combo_ids=[Command.set(combo.ids)])
        package_line = self._create_line(order, package)
        self.assertTrue(package_line._admits_line_promotion())
        item_line = self.lines.create({
            'order_id': order.id,
            'product_id': item_product.id,
            'linked_line_id': package_line.id,
            'combo_item_id': combo.combo_item_ids.id,
        })
        self.assertFalse(item_line._admits_line_promotion())

        invoiced = self._create_line(order, self.product_plain)
        invoiced.qty_invoiced = 1.0
        self.assertFalse(invoiced._admits_line_promotion())

    def test_line_eligibility_tint(self):
        from ..models.sale_tint import _TINT_OPERATION
        order = self._create_order()
        line = self._create_line(order, self.product_plain)
        self.assertTrue(line._admits_line_promotion())
        line.with_context(_tint_operation=_TINT_OPERATION).write({'tint_snapshot': {'base_only': True}})
        self.assertTrue(line._admits_line_promotion())
        self.assertFalse(line._get_line_promotion_block_reason())

    def test_constraint_rejects_scope_and_line(self):
        program = self._create_program([{'discount': 20.0}, {'discount': 5.0, 'discount_applicability': 'cheapest'}])
        in_scope = program.reward_ids.filtered(lambda reward: reward.discount == 20.0)
        out_of_scope = program.reward_ids - in_scope
        order = self._create_order()
        with self.assertRaises(ValidationError):
            self._create_line(order).promo_reward_id = out_of_scope

        fixed_product = self._create_product('Precio Fijo Restricción', 80.0)
        self._create_rule(fixed_product, fixed_price=70.0)
        fixed_line = self._create_line(order, fixed_product)
        with self.assertRaises(ValidationError):
            fixed_line.promo_reward_id = in_scope

    def test_available_rewards_order_and_specific(self):
        program = self._create_program([
            {'discount': 10.0},
            {'discount': 20.0, 'discount_applicability': 'specific',
             'discount_product_ids': [Command.set(self.product_plain.ids)]},
        ])
        order_reward = program.reward_ids.filtered(lambda reward: reward.discount == 10.0)
        specific_reward = program.reward_ids - order_reward
        order = self._create_order()
        agreement_line = self._create_line(order)
        plain_line = self._create_line(order, self.product_plain)

        available = order._get_line_promotion_rewards()
        self.assertEqual(available[agreement_line], order_reward)
        self.assertEqual(available[plain_line], order_reward | specific_reward)

        program.active = False
        order.invalidate_recordset()
        self.assertFalse(order._get_line_promotion_rewards()[plain_line])

    def test_apply_replaces_agreement(self):
        reward = self._create_program([{'discount': 20.0}]).reward_ids
        line = self._create_line(self._create_order())
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.pricing_rule_type, 'product')

        line._apply_line_promotion(reward)

        self.assertEqual(line.discount, 20.0)
        self.assertEqual(line.technical_discount, 20.0)
        self.assertEqual(line.price_subtotal, 80.0)
        self.assertEqual(line.pricing_rule_type, 'promo')
        self.assertEqual(line.pricing_rule_origin, 'Promo: Promo Línea (20.0%)')
        self.assertFalse(line.pricing_rule_id)
        self.assertEqual(line.promo_reward_id, reward)
        self.assertFalse(line.reward_id)
        self.assertFalse(line.coupon_id)
        self.assertFalse(line.points_cost)

    def test_apply_mixes_rewards_in_one_program(self):
        program = self._create_program([{'discount': 20.0}, {'discount': 10.0}])
        twenty = program.reward_ids.filtered(lambda reward: reward.discount == 20.0)
        order = self._create_order()
        first = self._create_line(order)
        second = self._create_line(order, self.product_plain)
        third = self._create_line(order, self._create_product('Rodillo Promo', 200.0))
        first._apply_line_promotion(twenty)
        second._apply_line_promotion(program.reward_ids - twenty)
        self.assertEqual((first.discount, second.discount, third.discount), (20.0, 10.0, 0.0))
        self.assertEqual(third.pricing_rule_type, 'none')

    def test_apply_replaces_manual_discount(self):
        reward = self._create_program([{'discount': 20.0}]).reward_ids
        line = self._create_line(self._create_order())
        line.discount = 7.0
        self.assertEqual(line.pricing_rule_type, 'manual')
        line._apply_line_promotion(reward)
        self.assertEqual(line.discount, 20.0)
        self.assertEqual(line.pricing_rule_type, 'promo')

    def test_apply_rejects_reward_not_offered(self):
        program = self._create_program([{
            'discount': 20.0,
            'discount_applicability': 'specific',
            'discount_product_ids': [Command.set(self.product_plain.ids)],
        }])
        order = self._create_order()
        line = self._create_line(order)
        with self.assertRaises(ValidationError):
            line._apply_line_promotion(program.reward_ids)
        self.assertEqual(line.discount, 10.0)
        self.assertFalse(line.promo_reward_id)

        unmet = self._create_program([{'discount': 30.0}], name='Promo Mínimo', minimum_amount=10000.0)
        with self.assertRaises(ValidationError):
            line._apply_line_promotion(unmet.reward_ids)

    def test_drop_restores_agreement(self):
        reward = self._create_program([{'discount': 20.0}]).reward_ids
        order = self._create_order()
        agreement_line = self._create_line(order)
        plain_line = self._create_line(order, self.product_plain)
        rule = agreement_line.pricing_rule_id
        (agreement_line | plain_line)._apply_line_promotion(reward)

        (agreement_line | plain_line)._drop_line_promotion()

        self.assertFalse(agreement_line.promo_reward_id)
        self.assertEqual(agreement_line.discount, 10.0)
        self.assertEqual(agreement_line.technical_discount, 10.0)
        self.assertEqual(agreement_line.pricing_rule_type, 'product')
        self.assertEqual(agreement_line.pricing_rule_origin, 'Desc. Producto (10.0%)')
        self.assertEqual(agreement_line.pricing_rule_id, rule)
        self.assertEqual(agreement_line.price_unit, 100.0)
        self.assertFalse(plain_line.promo_reward_id)
        self.assertEqual(plain_line.discount, 0.0)
        self.assertEqual(plain_line.pricing_rule_type, 'none')
        self.assertFalse(plain_line.pricing_rule_origin)

    def test_drop_keeps_manual_price(self):
        reward = self._create_program([{'discount': 20.0}]).reward_ids
        line = self._create_line(self._create_order())
        line.price_unit = 77.0
        self.assertEqual(line.price_origin, 'manual')
        line._apply_line_promotion(reward)
        line._drop_line_promotion()
        self.assertEqual(line.price_unit, 77.0)
        self.assertEqual(line.price_origin, 'manual')
        self.assertEqual(line.discount, 10.0)

    def _promo_line(self, discount=20.0):
        reward = self._create_program([{'discount': discount}]).reward_ids
        order = self._create_order()
        line = self._create_line(order)
        line._apply_line_promotion(reward)
        return order, line, reward

    def _assert_promo(self, line, reward, discount=20.0):
        self.assertEqual(line.discount, discount)
        self.assertEqual(line.technical_discount, discount)
        self.assertEqual(line.pricing_rule_type, 'promo')
        self.assertEqual(line.pricing_rule_origin, f'Promo: Promo Línea ({discount:.1f}%)')
        self.assertEqual(line.promo_reward_id, reward)

    def test_qty_change_keeps_promo(self):
        order, line, reward = self._promo_line()
        line.product_uom_qty = 3.0
        self._assert_promo(line, reward)
        self.assertEqual(line.price_subtotal, 240.0)

        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                line_form.product_uom_qty = 5.0
        self._assert_promo(line, reward)
        self.assertEqual(line.price_subtotal, 400.0)

    def test_uom_change_keeps_promo(self):
        order, line, reward = self._promo_line()
        line.product_uom_id = self.env.ref('uom.product_uom_dozen')
        self._assert_promo(line, reward)

    def test_update_prices_keeps_promo(self):
        order, line, reward = self._promo_line()
        order.action_update_prices()
        self._assert_promo(line, reward)
        self.assertEqual(line.price_subtotal, 80.0)

    def test_product_change_drops_promo(self):
        order, line, reward = self._promo_line()
        line.product_id = self.product_plain
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(line.pricing_rule_type, 'none')
        self.assertFalse(line.pricing_rule_origin)

    def test_product_change_in_form_drops_promo(self):
        order, line, reward = self._promo_line()
        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                line_form.product_id = self.product_plain
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(line.pricing_rule_type, 'none')

    def test_typed_discount_drops_promo(self):
        order, line, reward = self._promo_line()
        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                line_form.discount = 7.0
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.discount, 7.0)
        self.assertEqual(line.pricing_rule_type, 'manual')
        self.assertEqual(line.pricing_rule_origin, 'Desc. Manual (7.0%)')

    def test_untracked_discount_drops_promo(self):
        order, line, reward = self._promo_line()
        line.discount = 7.0
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.pricing_rule_type, 'manual')

        other_line = self._create_line(self._create_order())
        other_line._apply_line_promotion(reward)
        other_line.discount = 10.0
        self.assertFalse(other_line.promo_reward_id)
        self.assertEqual(other_line.pricing_rule_type, 'product')
        self.assertEqual(other_line.pricing_rule_origin, 'Desc. Producto (10.0%)')

    def test_discount_wizard_drops_promo(self):
        order, line, reward = self._promo_line()
        self.env['sale.order.discount'].create({
            'sale_order_id': order.id,
            'discount_type': 'sol_discount',
            'discount_percentage': 0.05,
        }).action_apply_discount()
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.discount, 5.0)
        self.assertEqual(line.pricing_rule_type, 'manual')

    def test_partner_fixed_price_drops_promo(self):
        order, line, reward = self._promo_line()
        other_partner = self.env['res.partner'].create({
            'name': 'Cliente Precio Fijo Promo',
            'is_customer': True,
            'phone': '1234567891',
            'property_product_pricelist': self.pricelist.id,
        })
        self.env['res.partner.discount.rule'].create({
            'partner_id': other_partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.product_agreement.id,
            'fixed_price': 60.0,
        })
        order.partner_id = other_partner
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.pricing_rule_type, 'fixed_price')
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(line.price_unit, 60.0)

    def test_partner_change_without_fixed_price_keeps_promo(self):
        order, line, reward = self._promo_line()
        other_partner = self.env['res.partner'].create({
            'name': 'Cliente Sin Acuerdo Promo',
            'is_customer': True,
            'phone': '1234567892',
            'property_product_pricelist': self.pricelist.id,
        })
        order.partner_id = other_partner
        self._assert_promo(line, reward)

    def test_copy_drops_promo(self):
        order, line, reward = self._promo_line()
        new_line = order.copy().order_line
        self.assertFalse(new_line.promo_reward_id)
        self.assertEqual(new_line.discount, 10.0)
        self.assertEqual(new_line.pricing_rule_type, 'product')
        self.assertEqual(new_line.pricing_rule_origin, 'Desc. Producto (10.0%)')

    def _save_line(self, order, line, **values):
        order.write({'order_line': [Command.update(line.id, values)]})

    def _minimum_promo(self, minimum=1000.0, qty=12.0):
        reward = self._create_program([{'discount': 10.0}], minimum_amount=minimum).reward_ids
        order = self._create_order()
        line = self._create_line(order, qty=qty)
        line._apply_line_promotion(reward)
        return order, line, reward

    def test_below_minimum_returns_to_agreement(self):
        order, line, reward = self._minimum_promo()
        self._save_line(order, line, product_uom_qty=9.0)
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.pricing_rule_type, 'product')
        self.assertEqual(line.pricing_rule_origin, 'Desc. Producto (10.0%)')

    def test_removed_line_below_minimum(self):
        reward = self._create_program([{'discount': 10.0}], minimum_amount=1000.0).reward_ids
        order = self._create_order()
        line = self._create_line(order, qty=9.0)
        other_line = self._create_line(order, self.product_plain, qty=4.0)
        line._apply_line_promotion(reward)
        order.write({'order_line': [Command.delete(other_line.id)]})
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.discount, 10.0)

    def test_minimum_ignores_own_promo(self):
        reward = self._create_program([{'discount': 10.0}], minimum_amount=1000.0).reward_ids
        order = self._create_order()
        line = self._create_line(order, self.product_plain, qty=20.0)
        line._apply_line_promotion(reward)
        self.assertEqual(line.price_subtotal, 900.0)
        order._update_programs_and_rewards()
        self.assertEqual(line.promo_reward_id, reward)
        self._save_line(order, line, name='Otra descripción')
        self.assertEqual(line.promo_reward_id, reward)
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.pricing_rule_type, 'promo')

    def test_minimum_counts_other_lines_net(self):
        reward = self._create_program([{'discount': 20.0}], minimum_amount=1000.0).reward_ids
        order = self._create_order()
        promo_line = self._create_line(order, self.product_plain, qty=2.0)
        agreement_line = self._create_line(order, qty=10.0)
        self.assertEqual(agreement_line.price_subtotal, 900.0)
        promo_line._apply_line_promotion(reward)
        order._update_programs_and_rewards()
        self.assertEqual(promo_line.promo_reward_id, reward)
        self._save_line(order, agreement_line, product_uom_qty=9.0)
        self.assertFalse(promo_line.promo_reward_id)

    def test_drop_cascades_to_other_program(self):
        lower = self._create_program([{'discount': 10.0}], name='Promo Mínimo', minimum_amount=1000.0).reward_ids
        ending = self._create_program([{'discount': 50.0}], name='Promo Que Termina').reward_ids
        order = self._create_order()
        lower_line = self._create_line(order, self.product_plain, qty=2.0)
        ending_line = self._create_line(order, qty=9.0)
        ending_line._apply_line_promotion(ending)
        lower_line._apply_line_promotion(lower)
        order._update_programs_and_rewards()
        self.assertEqual(lower_line.promo_reward_id, lower)

        # Con la otra promoción la línea cuenta 900; con su acuerdo de 10 % cuenta 810.
        ending.program_id.active = False
        order._update_programs_and_rewards()
        self.assertFalse(ending_line.promo_reward_id)
        self.assertFalse(lower_line.promo_reward_id)

    def _code_program(self, code='PROMO34', minimum=1000.0, program_type='promo_code'):
        return self.env['loyalty.program'].create({
            'name': 'Código Línea',
            'program_type': program_type,
            'trigger': 'with_code',
            'applies_on': 'current',
            'rule_ids': [Command.clear(), Command.create({
                'mode': 'with_code',
                'code': code,
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
                'minimum_amount': minimum,
            })],
            'reward_ids': [Command.clear(), Command.create({
                'reward_type': 'discount',
                'discount_mode': 'percent',
                'discount': 10.0,
                'discount_applicability': 'order',
                'required_points': 1,
            })],
        })

    def test_archived_program_returns_to_agreement(self):
        order, line, reward = self._promo_line()
        reward.program_id.active = False
        self._save_line(order, line, product_uom_qty=2.0)
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.pricing_rule_type, 'product')

    def test_reward_percent_update(self):
        order, line, reward = self._promo_line()
        reward.discount = 25.0
        self._save_line(order, line, product_uom_qty=2.0)
        self._assert_promo(line, reward, discount=25.0)
        self.assertEqual(line.price_subtotal, 150.0)

    def test_confirm_reevaluates(self):
        order, line, reward = self._minimum_promo()
        line.product_uom_qty = 9.0
        self.assertEqual(line.promo_reward_id, reward)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.discount, 10.0)

    def test_confirmed_order_is_frozen(self):
        order, line, reward = self._promo_line()
        order.action_confirm()
        self._assert_promo(line, reward)
        self.assertFalse(line.reward_id)

        reward.discount = 25.0
        self._save_line(order, line, product_uom_qty=2.0)
        self._assert_promo(line, reward)
        reward.program_id.active = False
        self._save_line(order, line, product_uom_qty=3.0)
        self._assert_promo(line, reward)
        self.assertEqual(line.price_subtotal, 240.0)

    def test_notice_posted(self):
        order, line, reward = self._minimum_promo()
        messages = order.message_ids
        self._save_line(order, line, product_uom_qty=9.0)
        notice = (order.message_ids - messages).filtered(lambda message: 'dejó la promoción' in message.body)
        self.assertEqual(len(notice), 1)
        self.assertIn(self.product_agreement.display_name, notice.body)
        self.assertIn('Promo Línea', notice.body)
        self.assertIn('el pedido ya no cumple la condición', notice.body)
        self.assertIn('Desc. Producto (10.0%)', notice.body)

    def test_no_notice_without_change(self):
        order, line, reward = self._minimum_promo()
        messages = order.message_ids
        self._save_line(order, line, product_uom_qty=11.0)
        self.assertFalse((order.message_ids - messages).filtered(
            lambda message: 'dejó la promoción' in (message.body or '')
        ))

    def _native_reward_order(self):
        """Pedido con un precio fijo de 70 y un producto de 50 sin acuerdo."""
        fixed_product = self._create_product('Precio Fijo Nativo', 80.0)
        self._create_rule(fixed_product, fixed_price=70.0)
        order = self._create_order()
        self._create_line(order, fixed_product)
        self._create_line(order, self.product_plain)
        return order

    def _claim_native_reward(self, order, reward):
        order._update_programs_and_rewards()
        claimable = order._get_claimable_rewards()
        coupon = next(coupon for coupon, rewards in claimable.items() if reward in rewards)
        self.assertFalse(order._apply_program_reward(reward, coupon))
        return order.order_line.filtered('reward_id')

    def test_capped_percent_stays_native(self):
        reward = self._create_program([{'discount': 10.0, 'discount_max_amount': 1000.0}]).reward_ids
        order = self._native_reward_order()
        reward_line = self._claim_native_reward(order, reward)
        self.assertEqual(reward_line.reward_id, reward)
        self.assertAlmostEqual(reward_line.price_unit, -5.0)
        self.assertFalse(order.order_line.promo_reward_id)
        self.assertEqual(order.amount_untaxed, 115.0)

    def test_cheapest_stays_native(self):
        reward = self._create_program([{'discount': 10.0, 'discount_applicability': 'cheapest'}]).reward_ids
        order = self._native_reward_order()
        reward_line = self._claim_native_reward(order, reward)
        self.assertEqual(reward_line.reward_id, reward)
        self.assertAlmostEqual(reward_line.price_unit, -5.0)

    def test_coupon_stays_native(self):
        program = self.env['loyalty.program'].create({
            'name': 'Cupón Nativo',
            'program_type': 'coupons',
            'trigger': 'with_code',
            'applies_on': 'current',
            'rule_ids': [Command.clear()],
            'reward_ids': [Command.clear(), Command.create({
                'reward_type': 'discount',
                'discount_mode': 'percent',
                'discount': 10.0,
                'discount_applicability': 'order',
                'required_points': 1,
            })],
        })
        card = self.env['loyalty.card'].create({'program_id': program.id, 'points': 1})
        order = self._native_reward_order()
        self.assertIn(card, order._try_apply_code(card.code))
        self.assertFalse(order._apply_program_reward(program.reward_ids, card))
        reward_line = order.order_line.filtered('reward_id')
        self.assertEqual(reward_line.reward_id, program.reward_ids)
        self.assertAlmostEqual(reward_line.price_unit, -5.0)
        self.assertEqual(reward_line.points_cost, 1)

    def test_line_rewards_not_claimable(self):
        program = self._create_program([
            {'discount': 20.0},
            {'reward_type': 'product', 'reward_product_id': self.product_plain.id},
        ])
        line_reward = program.reward_ids.filtered(lambda reward: reward.reward_type == 'discount')
        order = self._create_order()
        self._create_line(order)
        order._update_programs_and_rewards()
        claimable = self.env['loyalty.reward'].browse()
        for rewards in order._get_claimable_rewards().values():
            claimable |= rewards
        self.assertEqual(claimable, program.reward_ids - line_reward)

    def test_confirm_has_no_notice_for_line_rewards(self):
        self._create_program([{'discount': 20.0}])
        order = self._create_order()
        self._create_line(order)
        res = order.action_confirm()
        self.assertNotIsInstance(res, dict)


    def test_bulk_button_applies_best(self):
        program = self._create_program([
            {'discount': 15.0, 'discount_applicability': 'specific',
             'discount_product_ids': [Command.set(self.product_agreement.ids)]},
            {'discount': 20.0, 'discount_applicability': 'specific',
             'discount_product_ids': [Command.set(self.product_plain.ids)]},
        ])
        order_reward = program.reward_ids.filtered(lambda reward: reward.discount == 15.0)
        specific_reward = program.reward_ids - order_reward
        order = self._create_order()
        agreement_line = self._create_line(order)
        plain_line = self._create_line(order, self.product_plain)
        fixed_product = self._create_product('Precio Fijo Bloque', 80.0)
        self._create_rule(fixed_product, fixed_price=70.0)
        fixed_line = self._create_line(order, fixed_product)

        action = order.action_open_reward_wizard()

        self.assertEqual(action['tag'], 'display_notification')
        self.assertIn('2', action['params']['message'])
        self.assertEqual(action['params']['next']['tag'], 'soft_reload')
        self._assert_promo(agreement_line, order_reward, discount=15.0)
        self.assertEqual(plain_line.promo_reward_id, specific_reward)
        self.assertEqual(plain_line.discount, 20.0)
        self.assertEqual(fixed_line.pricing_rule_type, 'fixed_price')
        self.assertFalse(fixed_line.promo_reward_id)

    def test_bulk_button_keeps_better_agreement(self):
        better = self._create_product('Acuerdo Mayor', 100.0)
        self._create_rule(better, discount=25.0)
        equal = self._create_product('Acuerdo Igual', 100.0)
        self._create_rule(equal, discount=20.0)
        reward = self._create_program([{
            'discount': 20.0,
            'discount_applicability': 'specific',
            'discount_product_ids': [Command.set((better | self.product_plain | equal).ids)],
        }]).reward_ids
        order = self._create_order()
        better_line = self._create_line(order, better)
        manual_line = self._create_line(order, self.product_plain)
        manual_line.discount = 30.0
        equal_line = self._create_line(order, equal)

        action = order.action_open_reward_wizard()

        self.assertEqual(action['params']['type'], 'info')
        self.assertEqual(better_line.discount, 25.0)
        self.assertEqual(better_line.pricing_rule_type, 'product')
        self.assertEqual(manual_line.discount, 30.0)
        self.assertEqual(manual_line.pricing_rule_type, 'manual')
        self.assertEqual(equal_line.discount, 20.0)
        self.assertEqual(equal_line.pricing_rule_type, 'product')
        self.assertFalse((better_line | manual_line | equal_line).promo_reward_id)
        self.assertEqual(reward.program_id.order_count, 0)

    def test_bulk_button_upgrades_lower_promotion(self):
        program = self._create_program([{'discount': 20.0}, {'discount': 12.0}])
        twelve = program.reward_ids.filtered(lambda reward: reward.discount == 12.0)
        order = self._create_order()
        line = self._create_line(order)
        line._apply_line_promotion(twelve)
        order.action_open_reward_wizard()
        self.assertEqual(line.promo_reward_id, program.reward_ids - twelve)
        self.assertEqual(line.discount, 20.0)

    def test_bulk_button_opens_other_rewards(self):
        program = self._create_program([
            {'discount': 20.0},
            {'reward_type': 'product', 'reward_product_id': self.product_plain.id},
        ])
        line_reward = program.reward_ids.filtered(lambda reward: reward.reward_type == 'discount')
        order = self._create_order()
        line = self._create_line(order)

        action = order.action_open_reward_wizard()

        self.assertEqual(action['res_model'], 'sale.loyalty.reward.wizard')
        self.assertEqual(action['context']['active_id'], order.id)
        self.assertEqual(line.promo_reward_id, line_reward)
        native = self.env['sale.loyalty.reward.wizard'].with_context(active_id=order.id).create({})
        self.assertEqual(native.reward_ids, program.reward_ids - line_reward)


    def _tint_line(self, order):
        base_type = self.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        size = self.env['tint.size'].search([('code', '=', 'G')], limit=1)
        base = self.env['product.product'].create({
            'name': 'Base Promo',
            'default_code': 'TEST-PROMO-BASE',
            'tint_role': 'base',
            'tint_base_type_id': base_type.id,
            'tint_size_id': size.id,
            'list_price': 200,
            'taxes_id': [Command.clear()],
            'is_storable': True,
            'invoice_policy': 'order',
        })
        colorant = self.env['product.product'].create({
            'name': 'Colorante Promo',
            'default_code': 'TEST-PROMO-RED',
            'tint_role': 'colorant',
            'uom_id': self.env.ref('entintados_pdv.uom_tint_point').id,
            'list_price': 2,
            'taxes_id': [Command.clear()],
            'is_storable': True,
            'invoice_policy': 'order',
        })
        color = self.env['tint.color'].create({
            'name': 'Color Promo',
            'gallery_id': self.env['tint.gallery'].search([], limit=1).id,
            'code_number': '9875',
        })
        formula = self.env['tint.color.formula'].create({
            'color_id': color.id,
            'base_type_id': base_type.id,
            'size_id': size.id,
            'line_ids': [Command.create({'colorant_id': colorant.id, 'points': 12})],
        })
        return order._apply_tint(formula, base, 1)


    def test_bulk_button_with_tint_applies_to_all(self):
        reward = self._create_program([{'discount': 10.0}]).reward_ids
        order = self._create_order()
        tint_line = self._tint_line(order)
        brush_line = self._create_line(order, self.product_plain, qty=2.0)
        self.assertTrue(tint_line._admits_line_promotion())
        self.assertTrue(tint_line.promo_available_info)
        tint_price = tint_line.price_unit

        order.action_open_reward_wizard()

        self.assertEqual(brush_line.promo_reward_id, reward)
        self.assertEqual(brush_line.discount, 10.0)
        self.assertEqual(tint_line.promo_reward_id, reward)
        self.assertEqual((tint_line.discount, tint_line.price_unit), (10.0, tint_price))

    def test_bulk_button_tie_takes_lowest_id(self):
        program = self._create_program([{'discount': 15.0}, {'discount': 15.0}])
        order = self._create_order()
        line = self._create_line(order, self.product_plain)
        order.action_open_reward_wizard()
        self.assertEqual(line.promo_reward_id, program.reward_ids.sorted('id')[0])

    def test_open_without_line_rewards_is_native(self):
        order = self._create_order()
        self._create_line(order)
        self.assertNotIsInstance(order.action_open_reward_wizard(), dict)

    def test_open_confirmed_order_is_native(self):
        self._create_program([{'discount': 20.0}])
        order = self._create_order()
        self._create_line(order)
        order.action_confirm()
        self.assertNotIsInstance(order.action_open_reward_wizard(), dict)


    def test_available_count(self):
        program = self._create_program([{'discount': 20.0}])
        reward = program.reward_ids
        order = self._create_order()
        agreement_line = self._create_line(order)
        plain_line = self._create_line(order, self.product_plain)
        fixed_product = self._create_product('Precio Fijo Contador', 80.0)
        self._create_rule(fixed_product, fixed_price=70.0)
        fixed_line = self._create_line(order, fixed_product)

        self.assertEqual(order.promo_available_line_count, 2)
        self.assertEqual(agreement_line.promo_available_count, 1)
        self.assertEqual(fixed_line.promo_available_count, 0)

        agreement_line._apply_line_promotion(reward)
        self.assertEqual(agreement_line.promo_available_count, 0)
        self.assertEqual(plain_line.promo_available_count, 1)
        self.assertEqual(order.promo_available_line_count, 1)

        order.action_confirm()
        self.assertEqual(plain_line.promo_available_count, 0)
        self.assertEqual(order.promo_available_line_count, 0)

    def test_available_count_counts_other_reward_on_applied_line(self):
        program = self._create_program([{'discount': 20.0}, {'discount': 10.0}])
        order = self._create_order()
        line = self._create_line(order)
        line._apply_line_promotion(program.reward_ids[0])
        self.assertEqual(line.promo_available_count, 1)
        self.assertEqual(order.promo_available_line_count, 1)

    def test_available_count_without_programs(self):
        order = self._create_order()
        self._create_line(order)
        self.assertEqual(order.promo_available_line_count, 0)

    def test_available_count_updates_in_form(self):
        self._create_program([{'discount': 20.0}], minimum_amount=1000.0)
        order = self._create_order()
        self._create_line(order, self.product_plain, qty=1.0)
        with Form(order) as order_form:
            self.assertEqual(order_form.promo_available_line_count, 0)
            with order_form.order_line.edit(0) as line_form:
                line_form.product_uom_qty = 20.0
            self.assertEqual(order_form.promo_available_line_count, 1)

    def test_available_count_without_loyalty_access(self):
        self._create_program([{'discount': 20.0}])
        order = self._create_order()
        self._create_line(order)
        accountant = new_test_user(self.env, login='contador_promo', groups='account.group_account_invoice')
        self.assertFalse(self.env['loyalty.program'].with_user(accountant).has_access('read'))
        self.assertEqual(order.with_user(accountant).promo_available_line_count, 1)

    def test_view_decorations(self):
        arch = etree.fromstring(self.env['sale.order'].get_view(self.env.ref('sale.view_order_form').id)['arch'])
        origin_fields = arch.xpath("//field[@name='order_line']//field[@name='pricing_rule_origin']")
        self.assertEqual(len(origin_fields), 3)
        for field in origin_fields:
            self.assertEqual(field.get('decoration-primary'), "pricing_rule_type == 'promo'")
            self.assertEqual(field.get('decoration-warning'), "pricing_rule_type == 'manual'")

        active, disabled = arch.xpath("//button[@name='action_open_reward_wizard']")
        self.assertEqual(active.get('string'), 'Promociones')
        self.assertEqual(disabled.get('string'), 'Promociones')
        self.assertTrue(active.xpath(".//field[@name='promo_available_line_count']"))

    def test_available_info_difference(self):
        program = self._create_program([{'discount': 20.0}, {'discount': 5.0}], name='Promo TK')
        twenty = program.reward_ids.filtered(lambda reward: reward.discount == 20.0)
        five = program.reward_ids - twenty
        order = self._create_order()
        line = self._create_line(order, qty=3.0)
        self.assertEqual(line.promo_available_info, {
            'options': [
                {'reward_id': twenty.id, 'name': 'Promo TK', 'discount': 20.0, 'difference': 30.0, 'applied': False, 'scope': 'order'},
                {'reward_id': five.id, 'name': 'Promo TK', 'discount': 5.0, 'difference': -15.0, 'applied': False, 'scope': 'order'},
            ],
            'fallback': False,
        })

        order.action_confirm()
        self.assertFalse(line.promo_available_info)

    def test_available_info_with_applied(self):
        program = self._create_program([{'discount': 20.0}, {'discount': 5.0}], name='Promo TK')
        twenty = program.reward_ids.filtered(lambda reward: reward.discount == 20.0)
        five = program.reward_ids - twenty
        order = self._create_order()
        line = self._create_line(order, qty=3.0)
        line._apply_line_promotion(twenty)
        self.assertEqual(line.promo_available_info, {
            'options': [
                {'reward_id': twenty.id, 'name': 'Promo TK', 'discount': 20.0, 'difference': 0.0, 'applied': True, 'scope': 'order'},
                {'reward_id': five.id, 'name': 'Promo TK', 'discount': 5.0, 'difference': -45.0, 'applied': False, 'scope': 'order'},
            ],
            'fallback': {'label': 'Desc. Producto (10.0%)', 'difference': -30.0, 'scope': 'specific'},
        })
        self.assertEqual(line.promo_available_count, 1)

        only = self._create_program([{'discount': 30.0}], name='Promo Única').reward_ids
        program.active = False
        other_line = self._create_line(order, self.product_plain)
        other_line._apply_line_promotion(only)
        self.assertEqual(other_line.promo_available_count, 0)
        self.assertEqual(other_line.promo_available_info['fallback'], {'label': 'sin acuerdo', 'difference': -15.0, 'scope': 'specific'})

    def test_set_line_promotion(self):
        program = self._create_program([{'discount': 20.0}, {'discount': 15.0}])
        twenty = program.reward_ids.filtered(lambda reward: reward.discount == 20.0)
        order = self._create_order()
        line = self._create_line(order)
        self.assertTrue(line.action_set_line_promotion(twenty.id))
        self._assert_promo(line, twenty)

        line.action_set_line_promotion((program.reward_ids - twenty).id)
        self.assertEqual(line.promo_reward_id, program.reward_ids - twenty)
        self.assertEqual(line.discount, 15.0)

    def test_set_line_promotion_drop(self):
        order, line, reward = self._promo_line()
        line.action_set_line_promotion(False)
        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.pricing_rule_type, 'product')

    def test_set_line_promotion_rejects_not_offered(self):
        program = self._create_program([{
            'discount': 20.0,
            'discount_applicability': 'specific',
            'discount_product_ids': [Command.set(self.product_plain.ids)],
        }])
        order = self._create_order()
        line = self._create_line(order)
        with self.assertRaises(ValidationError):
            line.action_set_line_promotion(program.reward_ids.id)

        order.action_confirm()
        with self.assertRaises(UserError):
            line.action_set_line_promotion(False)

    def test_set_line_promotion_as_salesman(self):
        reward = self._create_program([{'discount': 20.0}]).reward_ids
        salesman = new_test_user(self.env, login='vendedor_estrella', groups='sales_team.group_sale_salesman')
        partner = self.env['res.partner'].create({
            'name': 'Cliente Estrella',
            'is_customer': True,
            'phone': '1234567894',
            'user_id': salesman.id,
            'property_product_pricelist': self.pricelist.id,
        })
        order = self.env['sale.order'].create({'partner_id': partner.id, 'pricelist_id': self.pricelist.id})
        line = self._create_line(order)
        line.with_user(salesman).action_set_line_promotion(reward.id)
        self.assertEqual(line.promo_reward_id, reward)

    def test_hint_in_views(self):
        arch = etree.fromstring(self.env['sale.order'].get_view(self.env.ref('sale.view_order_form').id)['arch'])
        hints = arch.xpath("//field[@name='order_line']//field[@name='promo_available_info'][@widget='promo_hint']")
        self.assertEqual(len(hints), 3)
        form_hint = arch.xpath("//field[@name='order_line']/form//field[@name='promo_available_info']")[0]
        self.assertIn("'selectable': false", form_hint.get('options'))
        kanban_card = arch.xpath("//field[@name='order_line']/kanban//field[@name='promo_available_info'][@widget]/parent::div")[0]
        self.assertIn('promo_available_info', kanban_card.get('invisible'))

    def test_minimum_and_quantity_rules(self):
        self._create_program([{'discount': 10.0}], minimum_amount=500.0)
        order = self._create_order()
        line = self._create_line(order, qty=2.0)
        self.assertFalse(order._get_line_promotion_rewards()[line])
        line.product_uom_qty = 10.0
        self.assertTrue(order._get_line_promotion_rewards()[line])

    def test_required_points_not_consumed(self):
        program = self._create_program([
            {'discount': 10.0},
            {'discount': 20.0},
            {'discount': 30.0, 'required_points': 2},
        ])
        too_expensive = program.reward_ids.filtered(lambda reward: reward.required_points == 2)
        offered = program.reward_ids - too_expensive
        order = self._create_order()
        first = self._create_line(order, self.product_plain)
        second = self._create_line(order)
        self.assertEqual(order._get_line_promotion_rewards()[first], offered)

        first.promo_reward_id = offered[0]
        self.assertEqual(order._get_line_promotion_rewards()[second], offered)

    def test_confirm_keeps_promo_with_minimum(self):
        # Promoción con importe mínimo que solo se cumple antes de su propio descuento, junto a una
        # promoción nativa en el pedido: la promoción de línea se conserva al confirmar.
        paint_no_agree = self._create_product('Pintura Sin Acuerdo', 100.0)
        line_prog = self._create_program([{
            'discount': 10.0,
            'discount_applicability': 'specific',
            'discount_product_ids': [Command.set(paint_no_agree.ids)],
        }], name='Promo Mínimo', minimum_amount=1000.0)
        native_prog = self.env['loyalty.program'].create({
            'name': 'Nativa Barato',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [Command.create({
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
            })],
            'reward_ids': [Command.create({
                'reward_type': 'discount',
                'discount_mode': 'percent',
                'discount': 10.0,
                'discount_applicability': 'cheapest',
                'required_points': 1,
            })],
        })
        order = self._create_order()
        line_paint = self._create_line(order, paint_no_agree, qty=10.0)  # 10 x 100 = 1000
        self._create_line(order, self.product_plain, qty=1.0)           # 1 x 50 = 50
        order._update_programs_and_rewards()

        claimable = order._get_claimable_rewards()
        for coupon, rewards in claimable.items():
            for reward in rewards:
                if reward == native_prog.reward_ids:
                    order._apply_program_reward(reward, coupon)

        order._update_programs_and_rewards()
        line_paint._apply_line_promotion(line_prog.reward_ids)
        self.assertEqual(line_paint.promo_reward_id, line_prog.reward_ids)
        self.assertEqual(line_paint.discount, 10.0)

        order.action_confirm()

        self.assertEqual(order.state, 'sale')
        self.assertEqual(line_paint.promo_reward_id, line_prog.reward_ids)
        self.assertEqual(line_paint.discount, 10.0)
        self.assertEqual(line_paint.pricing_rule_type, 'promo')

    def test_unlink_reward_in_use_protected(self):
        # Programa con múltiples recompensas: borrar una recompensa en uso la archiva y conserva la referencia en la línea.
        prog = self._create_program([{'discount': 20.0}, {'discount': 5.0}])
        twenty = prog.reward_ids.filtered(lambda r: r.discount == 20.0)
        order = self._create_order()
        line = self._create_line(order)
        line.action_set_line_promotion(twenty.id)
        self.assertEqual(line.promo_reward_id, twenty)
        self.assertEqual(line.discount, 20.0)
        self.assertEqual(line.pricing_rule_type, 'promo')

        twenty.unlink()

        self.assertTrue(twenty.exists(), "La recompensa no debe eliminarse físicamente de la base de datos si está en uso")
        self.assertFalse(twenty.active, "La recompensa en uso debe quedar archivada")
        self.assertEqual(line.promo_reward_id, twenty)

    def test_unlink_archived_program_in_use_cleans_lines(self):
        # Al archivar y borrar un programa, las líneas con su promoción deben volver a su acuerdo vigente.
        prog = self._create_program([{'discount': 20.0}])
        order = self._create_order()
        line = self._create_line(order)
        line.action_set_line_promotion(prog.reward_ids.id)
        self.assertEqual(line.promo_reward_id, prog.reward_ids)
        self.assertEqual(line.discount, 20.0)

        prog.action_archive()
        prog.unlink()

        self.assertFalse(line.promo_reward_id)
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.pricing_rule_type, 'product')

    def test_change_partner_with_discount_rule_no_phantom_promo(self):
        # Al cambiar de cliente a uno con acuerdo comercial de porcentaje, la línea conserva su promoción válida sin degradar a promoción fantasma.
        order, line, reward = self._promo_line()
        self.assertEqual(line.promo_reward_id, reward)
        self.assertEqual(line.discount, 20.0)

        partner_b = self.env['res.partner'].create({
            'name': 'Cliente B con Acuerdo 15%',
            'is_customer': True,
            'phone': '1234567895',
            'property_product_pricelist': self.pricelist.id,
        })
        self.env['res.partner.discount.rule'].create({
            'partner_id': partner_b.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.product_agreement.id,
            'discount': 15.0,
        })

        order.write({'partner_id': partner_b.id})

        if line.pricing_rule_type == 'promo':
            self.assertTrue(line.promo_reward_id, "La línea en promo debe conservar promo_reward_id, no ser una promo fantasma")
            self.assertEqual(line.promo_reward_id, reward)
            self.assertEqual(line.discount, 20.0)
        else:
            self.assertEqual(line.pricing_rule_type, 'product')
            self.assertEqual(line.discount, 15.0)
            self.assertFalse(line.promo_reward_id)

    def test_promo_code_minimum_threshold_deterministic(self):
        # El umbral de compra mínima de un código se evalúa sin el descuento de la promoción de línea, garantizando determinismo.
        auto_prog = self._create_program([{'discount': 20.0}])
        code_prog = self._code_program(code='CODE199', minimum=1000.0)

        order = self._create_order()
        line = self._create_line(order, qty=10.0)  # 10 x 100 = 1000

        line._apply_line_promotion(auto_prog.reward_ids)
        self.assertEqual(line.discount, 20.0)
        self.assertEqual(line.price_subtotal, 800.0)

        res = order._try_apply_code('CODE199')
        self.assertNotIn('error', res, f"El código debió aceptarse pero dio error: {res}")
        self.assertIn(code_prog.rule_ids.id, order.code_enabled_rule_ids.ids)

    def test_drop_promotion_restores_custom_or_agreement_price(self):
        # Quitar la promoción no debe forzar precio de lista general
        prod = self._create_product('Producto Acuerdo 120', 120.0)
        self._create_rule(prod, discount=10.0)
        order = self._create_order()
        line = self._create_line(order, prod)
        line.price_unit = 100.0  # precio personalizado
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.pricing_rule_type, 'product')

        reward = self._create_program([{'discount': 20.0}]).reward_ids
        line._apply_line_promotion(reward)
        self.assertEqual(line.discount, 20.0)
        self.assertEqual(line.pricing_rule_type, 'promo')

        # Popover simulation
        order.invalidate_recordset()
        line.invalidate_recordset(['promo_available_info'])
        info = line.promo_available_info
        self.assertTrue(info and info.get('fallback'))

        # Quitar la promoción
        line._drop_line_promotion()
        self.assertEqual(line.price_unit, 100.0, "El precio unitario no debe saltar a 120.0 (precio de lista)")
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.pricing_rule_type, 'product')

        # Popover con regla de precio fijo calcula la diferencia exacta contra el subtotal real
        prod_fixed = self._create_product('Producto Fixed 100', 100.0)
        self._create_rule(prod_fixed, fixed_price=70.0)
        order_fixed = self._create_order()
        line_fixed = self._create_line(order_fixed, prod_fixed)
        line_fixed.with_context(skip_manual_discount_breakdown=True).write({
            'promo_reward_id': reward.id,
            'discount': 20.0,
            'price_unit': 100.0,
            'pricing_rule_type': 'promo',
        })
        line_fixed.invalidate_recordset(['promo_available_info', 'price_subtotal'])
        info_fixed = line_fixed.promo_available_info
        self.assertEqual(info_fixed['fallback']['difference'], 10.0)
        line_fixed._drop_line_promotion()
        self.assertEqual(line_fixed.price_unit, 70.0)
        self.assertEqual(line_fixed.discount, 0.0)
        self.assertEqual(line_fixed.pricing_rule_type, 'fixed_price')

    def test_program_max_usage_enforced_strictly_under_all_roles(self):
        # El límite de usos se evalúa bajo sudo() para todos los roles
        program = self._create_program([{'discount': 20.0}])
        program.write({'limit_usage': True, 'max_usage': 1})
        reward = program.reward_ids

        order1 = self._create_order()
        line1 = self._create_line(order1)
        line1._apply_line_promotion(reward)
        self.env.flush_all()

        user_salesman = new_test_user(self.env, login='salesman_promo_test', groups='sales_team.group_sale_salesman')
        order2 = self._create_order()
        line2 = self._create_line(order2, self.product_plain)
        available = order2.with_user(user_salesman)._get_line_promotion_rewards()
        self.assertNotIn(reward, available.get(line2, self.env['loyalty.reward']),
                         "La promoción no debe estar disponible porque ya alcanzó max_usage=1")

    def _limited_program_used_by_other_salesman(self, rewards):
        """Programa con un solo uso, ya consumido en la cotización del vendedor A.

        Devuelve el programa, el vendedor B (mismo grupo que A, sin acceso a sus pedidos) y una
        cotización propia de B con un producto sin acuerdo.
        """
        program = self._create_program(rewards)
        program.write({'limit_usage': True, 'max_usage': 1})
        salesman_a = new_test_user(self.env, login='vendedor_limite_a', groups='sales_team.group_sale_salesman')
        salesman_b = new_test_user(self.env, login='vendedor_limite_b', groups='sales_team.group_sale_salesman')
        orders = self.env['sale.order']
        for salesman in (salesman_a, salesman_b):
            partner = self.env['res.partner'].create({
                'name': f'Cliente de {salesman.login}',
                'is_customer': True,
                'phone': '1234567890',
                'user_id': salesman.id,
            })
            orders |= self.env['sale.order'].create({
                'partner_id': partner.id,
                'pricelist_id': self.pricelist.id,
                'user_id': salesman.id,
            })
        order_a, order_b = orders
        line_a = self._create_line(order_a, self.product_plain)
        line_b = self._create_line(order_b, self.product_plain)
        return program, salesman_b, order_a, line_a, order_b, line_b

    def test_max_usage_not_exceeded_via_promotions_button(self):
        program, salesman_b, __, line_a, order_b, line_b = self._limited_program_used_by_other_salesman(
            [{'discount': 20.0}]
        )
        line_a._apply_line_promotion(program.reward_ids)
        self.env.flush_all()
        # El conteo se calcula por primera vez con el usuario que pulsa el botón.
        self.env.invalidate_all()

        order_b.with_user(salesman_b).action_open_reward_wizard()

        self.assertFalse(line_b.promo_reward_id)
        self.assertNotEqual(line_b.discount, 20.0)
        self.env.invalidate_all()
        self.assertEqual(program.sudo().total_order_count, 1)

    def test_native_reward_max_usage_not_exceeded_by_restricted_salesman(self):
        program, salesman_b, order_a, __, order_b, __ = self._limited_program_used_by_other_salesman(
            [{'discount': 10.0, 'discount_max_amount': 5.0}]
        )
        native_reward = program.reward_ids
        self.assertFalse(native_reward._is_line_promotion())
        reward_product = self._create_product('Descuento Pedido Límite', 0.0)
        self.lines.create({
            'order_id': order_a.id,
            'product_id': reward_product.id,
            'name': 'Descuento Pedido Límite',
            'product_uom_qty': 1.0,
            'price_unit': -5.0,
            'is_reward_line': True,
            'reward_id': native_reward.id,
        })
        self.env.flush_all()
        self.env.invalidate_all()

        order_b_as_b = order_b.with_user(salesman_b)
        order_b_as_b._update_programs_and_rewards()
        claimable = order_b_as_b._get_claimable_rewards()

        self.assertNotIn(native_reward, self.env['loyalty.reward'].union(*claimable.values()))
        self.assertFalse(order_b.order_line.filtered(lambda line: line.reward_id.program_id == program))
        self.env.invalidate_all()
        self.assertEqual(program.sudo().total_order_count, 1)

    def test_order_discount_totals_include_fixed_reward_line(self):
        # Descuento total e Importe antes de descuento incluyen recompensas de importe fijo
        order = self._create_order()
        prod_line = self._create_line(order, qty=1.0)  # 1 x 100.0 = 100.0 con 10% acuerdo = 90.0
        self.assertEqual(order.amount_untaxed, 90.0)

        # Simular línea de recompensa de importe fijo
        program = self._create_program([{'discount': 10.0}])
        reward = program.reward_ids[0]
        reward_prod = self._create_product('Recompensa $10', 0.0)
        reward_line = self.lines.create({
            'order_id': order.id,
            'product_id': reward_prod.id,
            'name': 'Recompensa $10',
            'product_uom_qty': 1.0,
            'price_unit': -10.0,
            'price_subtotal': -10.0,
            'reward_id': reward.id,
            'is_reward_line': True,
        })
        self.env.flush_all()
        order.invalidate_recordset()
        self.assertEqual(order.amount_discount, 20.0)
        self.assertEqual(order.amount_before_discount, 100.0)

    def test_action_update_prices_no_spurious_chatter_warning(self):
        # Actualizar precios no debe generar aviso falso en el chatter
        order, line, reward = self._promo_line(discount=20.0)
        initial_msg_count = len(order.message_ids)

        order.action_update_prices()
        new_msgs = order.message_ids - order.message_ids.browse([m.id for m in order.message_ids[:initial_msg_count]])
        spurious = any("dejó la promoción" in (m.body or '') or "0 %" in (m.body or '') for m in new_msgs)
        self.assertFalse(spurious, "No debe publicarse aviso de pérdida de promoción o 0% en el chatter")

    def test_applied_programs_includes_native_reward_program(self):
        # Pedido que ya usa el programa con recompensa nativa puede usar la promo de línea si el límite está lleno
        program = self._create_program([{'discount': 20.0}])
        program.write({'limit_usage': True, 'max_usage': 1})
        line_reward = program.reward_ids[0]
        native_reward = self.env['loyalty.reward'].create({
            'program_id': program.id,
            'reward_type': 'discount',
            'discount_mode': 'percent',
            'discount': 10.0,
            'discount_applicability': 'order',
            'required_points': 1,
        })

        order = self._create_order()
        line = self._create_line(order)
        reward_prod = self._create_product('Descuento Orden 10%', 0.0)
        self.lines.create({
            'order_id': order.id,
            'product_id': reward_prod.id,
            'name': 'Descuento Orden 10%',
            'product_uom_qty': 1.0,
            'price_unit': -9.0,
            'price_subtotal': -9.0,
            'is_reward_line': True,
            'reward_id': native_reward.id,
        })

        available = order._get_line_promotion_rewards()
        self.assertIn(line_reward, available.get(line, self.env['loyalty.reward']),
                      "La línea debe poder usar la recompensa de línea porque la orden ya participa en el programa")

    def test_money_points_exact_thousand_float_round(self):
        # Con puntos por dinero, suma exacta de 1,000.00 con decimales califica
        program = self.env['loyalty.program'].create({
            'name': 'Puntos Dinero 1000',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [Command.create({
                'reward_point_mode': 'money',
                'reward_point_amount': 1,
            })],
            'reward_ids': [Command.create({
                'reward_type': 'discount',
                'discount_mode': 'percent',
                'discount': 20.0,
                'discount_applicability': 'order',
                'required_points': 1000,
            })],
        })
        order = self._create_order()
        p1 = self._create_product('P1', 32.91)
        p2 = self._create_product('P2', 257.28)
        p3 = self._create_product('P3', 709.81)
        self._create_line(order, p1)
        self._create_line(order, p2)
        line3 = self._create_line(order, p3)

        points = order._get_line_promotion_points(program)
        self.assertEqual(points, 1000.0, f"Los puntos deberían ser 1000.0 pero dieron {points}")
        available = order._get_line_promotion_rewards()
        self.assertIn(program.reward_ids, available.get(line3, self.env['loyalty.reward']))

    def test_promo_available_count_matches_unapplied_lines(self):
        # promo_available_line_count cuenta sólo líneas pendientes
        program = self._create_program([{'discount': 20.0}])
        reward = program.reward_ids
        order = self._create_order()
        line_a = self._create_line(order)
        line_b = self._create_line(order, self.product_plain)

        self.assertEqual(order.promo_available_line_count, 2)
        line_a._apply_line_promotion(reward)
        self.assertEqual(order.promo_available_line_count, 1)

        self.assertTrue(line_a.promo_available_info)
        self.assertTrue(line_b.promo_available_info)

    def test_promo_code_is_native(self):
        program = self._code_program(minimum=0.0)
        reward = program.reward_ids
        self.assertFalse(reward._is_line_promotion())
        order = self._native_reward_order()
        self.assertNotIn('error', order._try_apply_code('PROMO34'))
        self.assertFalse(any(order._get_line_promotion_rewards().values()))
        self.assertEqual(order.promo_available_line_count, 0)
        reward_line = self._claim_native_reward(order, reward)
        self.assertEqual(reward_line.reward_id, reward)
        # El descuento nativo no alcanza el precio fijo: solo el 10 % de 50.
        self.assertAlmostEqual(reward_line.price_unit, -5.0)
        self.assertFalse(order.order_line.promo_reward_id)

    def _order_promo_setup(self, **rule):
        """Cliente con 15 % de acuerdo en P1, sin acuerdo en P2 ni P3. «Pedido» da 10 % en todo
        el pedido y «Esp» da 20 % solo en P3."""
        products = {
            name: self._create_product(f'{name} Pedido', 100.0)
            for name in ('P1', 'P2', 'P3', 'P4')
        }
        self._create_rule(products['P1'], discount=15.0)
        order_reward = self._create_program([{'discount': 10.0}], name='Pedido', **rule).reward_ids
        specific_reward = self._create_program([{
            'discount': 20.0,
            'discount_applicability': 'specific',
            'discount_product_ids': [Command.set(products['P3'].ids)],
        }], name='Esp').reward_ids
        order = self._create_order()
        lines = [self._create_line(order, products[name]) for name in ('P1', 'P2', 'P3')]
        return order, lines, order_reward, specific_reward, products

    def _discounts(self, lines):
        return [line.discount for line in lines]

    def _assert_order_promo(self, order, lines, reward):
        self.assertEqual(order.order_promo_reward_id, reward)
        for line in lines:
            self.assertEqual(line.promo_reward_id, reward)
            self.assertEqual(line.discount, reward.discount)
            self.assertEqual(line.pricing_rule_type, 'promo')

    def test_order_promo_applies_to_all_lines(self):
        order, lines, order_reward, __, __ = self._order_promo_setup()
        self.assertEqual(self._discounts(lines), [15.0, 0.0, 0.0])
        lines[1].action_set_line_promotion(order_reward.id)
        # También P1, aunque su acuerdo de 15 % dé más: la promoción o el acuerdo, no los dos.
        self._assert_order_promo(order, lines, order_reward)
        self.assertEqual(lines[0].pricing_rule_origin, 'Promo: Pedido (10.0%)')

    def test_order_promo_new_line_on_save(self):
        order, lines, order_reward, __, products = self._order_promo_setup()
        lines[1].action_set_line_promotion(order_reward.id)
        before = order.order_line
        order.write({'order_line': [Command.create({'product_id': products['P4'].id, 'product_uom_qty': 1.0})]})
        self._assert_order_promo(order, order.order_line - before, order_reward)

        better_product = self._create_product('P5 Pedido', 100.0)
        self._create_rule(better_product, discount=15.0)
        before = order.order_line
        order.write({'order_line': [Command.create({'product_id': better_product.id, 'product_uom_qty': 1.0})]})
        self._assert_order_promo(order, order.order_line - before, order_reward)

    def test_order_promo_remove_from_order(self):
        order, (l1, l2, l3), order_reward, __, __ = self._order_promo_setup()
        l2.action_set_line_promotion(order_reward.id)
        l3.action_set_line_promotion(False)
        self.assertFalse(order.order_promo_reward_id)
        self.assertFalse(order.order_line.promo_reward_id)
        self.assertEqual(self._discounts((l1, l2, l3)), [15.0, 0.0, 0.0])
        self.assertEqual(l1.pricing_rule_type, 'product')
        self._save_line(order, l2, product_uom_qty=2.0)
        self.assertFalse(order.order_line.promo_reward_id)

    def test_order_promo_blocks_typed_discount(self):
        order, (l1, l2, l3), order_reward, __, __ = self._order_promo_setup()
        l2.action_set_line_promotion(order_reward.id)
        self.assertFalse(l2.manual_discount_allowed)
        with self.assertRaises(UserError):
            l2.discount = 5.0
        with Form(order) as order_form:
            with order_form.order_line.edit(1) as line_form:
                with self.assertRaises(AssertionError):
                    line_form.discount = 5.0
        self._assert_order_promo(order, (l1, l2, l3), order_reward)

    def test_order_promo_product_change_keeps_promo(self):
        order, (l1, l2, l3), order_reward, __, products = self._order_promo_setup()
        l2.action_set_line_promotion(order_reward.id)
        self._save_line(order, l2, product_id=products['P4'].id)
        self._assert_order_promo(order, (l1, l2, l3), order_reward)

    def test_order_promo_replaced_by_other(self):
        order, lines, order_reward, __, __ = self._order_promo_setup()
        other_reward = self._create_program([{'discount': 12.0}], name='Pedido Dos').reward_ids
        lines[1].action_set_line_promotion(order_reward.id)
        lines[2].action_set_line_promotion(other_reward.id)
        self._assert_order_promo(order, lines, other_reward)

    def test_order_promo_lost_clears_order(self):
        # Cada línea cuenta 100 sin la promoción propia: 300 cumple el mínimo y 200 ya no.
        order, (l1, l2, l3), order_reward, __, __ = self._order_promo_setup(minimum_amount=250.0)
        l3.action_set_line_promotion(order_reward.id)
        self._assert_order_promo(order, (l1, l2, l3), order_reward)
        messages = order.message_ids
        order.write({'order_line': [Command.delete(l1.id)]})
        self.assertFalse(order.order_promo_reward_id)
        self.assertFalse(order.order_line.promo_reward_id)
        self.assertEqual(self._discounts((l2, l3)), [0.0, 0.0])
        notices = (order.message_ids - messages).filtered(
            lambda message: 'El pedido dejó la promoción Pedido' in (message.body or '')
        )
        self.assertEqual(len(notices), 1)

    def test_order_promo_blocks_specific(self):
        order, (l1, l2, l3), order_reward, specific_reward, __ = self._order_promo_setup()
        self.assertIn(specific_reward, order._get_line_promotion_rewards()[l3])
        l2.action_set_line_promotion(order_reward.id)
        self.assertNotIn(specific_reward, order._get_line_promotion_rewards()[l3])
        with self.assertRaises(UserError):
            l3.action_set_line_promotion(specific_reward.id)
        self._assert_order_promo(order, (l1, l2, l3), order_reward)

    def test_order_promo_replaces_specific(self):
        order, (l1, l2, l3), order_reward, specific_reward, __ = self._order_promo_setup()
        l3.action_set_line_promotion(specific_reward.id)
        self.assertEqual(l3.discount, 20.0)
        l2.action_set_line_promotion(order_reward.id)
        self._assert_order_promo(order, (l1, l2, l3), order_reward)

    def test_bulk_button_prefers_order_promo(self):
        order, lines, order_reward, __, __ = self._order_promo_setup()
        order.action_open_reward_wizard()
        self._assert_order_promo(order, lines, order_reward)

    def test_bulk_button_specific_without_order_promo(self):
        order, (l1, l2, l3), order_reward, specific_reward, __ = self._order_promo_setup()
        order_reward.program_id.active = False
        order.action_open_reward_wizard()
        self.assertFalse(order.order_promo_reward_id)
        self.assertEqual(self._discounts((l1, l2, l3)), [15.0, 0.0, 20.0])
        self.assertEqual(l1.pricing_rule_type, 'product')
        self.assertEqual(l3.promo_reward_id, specific_reward)

    def test_order_promo_blocks_discount_wizard(self):
        order, lines, order_reward, __, __ = self._order_promo_setup()
        lines[1].action_set_line_promotion(order_reward.id)
        wizard = self.env['sale.order.discount'].create({
            'sale_order_id': order.id,
            'discount_type': 'sol_discount',
            'discount_percentage': 0.05,
        })
        with self.assertRaises(UserError):
            wizard.action_apply_discount()
        self._assert_order_promo(order, lines, order_reward)

    def test_available_info_with_order_promo(self):
        order, (l1, l2, l3), order_reward, specific_reward, __ = self._order_promo_setup()
        l2.action_set_line_promotion(order_reward.id)
        info = l3.promo_available_info
        self.assertEqual([option['reward_id'] for option in info['options']], [order_reward.id])
        self.assertEqual(info['options'][0]['scope'], 'order')
        self.assertTrue(info['options'][0]['applied'])
        self.assertEqual(info['fallback']['scope'], 'order')
        self.assertNotIn(specific_reward.id, [option['reward_id'] for option in info['options']])

    def test_order_promo_copy_resets(self):
        order, lines, order_reward, __, __ = self._order_promo_setup()
        lines[1].action_set_line_promotion(order_reward.id)
        copy = order.copy()
        self.assertFalse(copy.order_promo_reward_id)
        self.assertFalse(copy.order_line.promo_reward_id)
        self.assertEqual(sorted(copy.order_line.mapped('discount')), [0.0, 0.0, 15.0])


@tagged('post_install', '-at_install')
class TestSaleComboPromotion(TransactionCase):
    """El paquete de un combo admite promociones como cualquier línea: el dominio se evalúa con
    el producto combo y sus productos copian el descuento del paquete."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
            | cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
            | cls.env.ref('entintados_pdv.group_sale_edit_discount')
        )
        cls.env['loyalty.program'].search([]).active = False
        cls.lines = cls.env['sale.order.line']
        products = cls.env['product.product']

        def product(name, price, **values):
            return products.create({
                'name': name,
                'list_price': price,
                'invoice_policy': 'order',
                'taxes_id': [Command.clear()],
                **values,
            })

        cls.vinilica = product('Vinílica Combo Promo', 500.0)
        cls.rodillo = product('Rodillo Combo Promo', 150.0)
        cls.brocha = product('Brocha Combo Promo', 50.0)
        cls.suelto = product('Suelto Combo Promo', 100.0)
        cls.extra = product('Extra Combo Promo', 100.0)
        cls.combos = cls.env['product.combo'].create([
            {'name': name, 'combo_item_ids': [Command.create({'product_id': item.id})]}
            for name, item in (('Pintura', cls.vinilica), ('Rodillo', cls.rodillo), ('Brocha', cls.brocha))
        ])
        cls.package = product(
            'Paquete Combo Promo', 1000.0, type='combo', combo_ids=[Command.set(cls.combos.ids)],
        )
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Combo Promo',
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Combo Promo',
            'is_customer': True,
            'phone': '1234567890',
        })
        cls.partner.property_product_pricelist = cls.pricelist
        cls.package_rule = cls.env['res.partner.discount.rule'].create({
            'partner_id': cls.partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': cls.package.id,
            'discount': 15.0,
        })

    @classmethod
    def _create_program(cls, discount, name, products=None, **rule):
        reward = {
            'reward_type': 'discount',
            'discount_mode': 'percent',
            'discount': discount,
            'discount_applicability': 'specific' if products else 'order',
            'required_points': 1,
        }
        if products:
            reward['discount_product_ids'] = [Command.set(products.ids)]
        return cls.env['loyalty.program'].create({
            'name': name,
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
                **rule,
            })],
            'reward_ids': [Command.clear(), Command.create(reward)],
        }).reward_ids

    def _create_order(self):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
        })

    def _quote(self, order=None, qty=1.0):
        """Paquete y productos en un solo `write`, como el guardado del formulario."""
        order = order or self._create_order()
        order.write({'order_line': [
            Command.create({'product_id': self.package.id, 'product_uom_qty': qty, 'virtual_id': 'combo-1'}),
            *[Command.create({
                'product_id': combo.combo_item_ids.product_id.id,
                'product_uom_qty': qty,
                'combo_item_id': combo.combo_item_ids.id,
                'linked_virtual_id': 'combo-1',
            }) for combo in self.combos],
        ]})
        package_line = order.order_line.filtered(lambda line: line.product_id == self.package)
        return order, package_line, package_line.linked_line_ids.filtered('combo_item_id')

    def _assert_combo(self, package_line, items, discount, rule_type, origin, reward=None):
        self.assertEqual(
            (package_line.discount, package_line.pricing_rule_type, package_line.pricing_rule_origin),
            (discount, rule_type, origin),
        )
        self.assertEqual(package_line.promo_reward_id, reward or self.env['loyalty.reward'])
        self.assertEqual(len(items), 3)
        for line in items:
            self.assertEqual(
                (line.discount, line.technical_discount, line.pricing_rule_type, line.pricing_rule_origin),
                (discount, discount, rule_type, f'Del paquete: {origin}'),
                line.product_id.name,
            )
            self.assertFalse(line.promo_reward_id)

    def test_specific_promo_on_combo_package(self):
        reward = self._create_program(10.0, 'Esp', self.package)
        order, package_line, items = self._quote()
        self._assert_combo(package_line, items, 15.0, 'product', 'Desc. Producto (15.0%)')
        self.assertEqual(order._get_line_promotion_rewards()[package_line], reward)
        package_line.action_set_line_promotion(reward.id)
        self._assert_combo(package_line, items, 10.0, 'promo', 'Promo: Esp (10.0%)', reward)
        self.assertAlmostEqual(order.amount_untaxed, 900.0, places=2)
        for line in items:
            self.assertFalse(line._is_listed_for_line_promotion())

    def test_combo_items_show_package_promo(self):
        reward = self._create_program(10.0, 'Esp', self.package)
        __, package_line, items = self._quote()
        self.assertTrue(package_line.promo_available_info)
        for line in items:
            self.assertFalse(line.promo_available_info)

        package_line.action_set_line_promotion(reward.id)
        (package_line | items).invalidate_recordset(['promo_available_info'])
        for line in items:
            self.assertEqual(line.promo_available_info, {
                'options': [{
                    'reward_id': reward.id,
                    'name': 'Esp',
                    'discount': 10.0,
                    'difference': 0.0,
                    'applied': True,
                    'scope': 'specific',
                }],
                'fallback': False,
                'from_package': True,
            })
            self.assertEqual(line.promo_available_count, 0)

        package_line.action_set_line_promotion(False)
        items.invalidate_recordset(['promo_available_info'])
        for line in items:
            self.assertFalse(line.promo_available_info)

    def test_specific_promo_on_combo_item_not_offered(self):
        reward = self._create_program(10.0, 'Esp Vinílica', self.vinilica)
        order, package_line, items = self._quote()
        self.assertTrue(package_line._admits_line_promotion())
        available = order._get_line_promotion_rewards()
        for line in package_line | items:
            self.assertNotIn(reward, available[line])
        with self.assertRaises(ValidationError):
            package_line._apply_line_promotion(reward)

    def test_order_promo_reaches_combo(self):
        reward = self._create_program(10.0, 'Pedido')
        order, package_line, items = self._quote()
        loose = self.lines.create({'order_id': order.id, 'product_id': self.suelto.id, 'product_uom_qty': 1.0})
        loose.action_set_line_promotion(reward.id)
        self.assertEqual(order.order_promo_reward_id, reward)
        self._assert_combo(package_line, items, 10.0, 'promo', 'Promo: Pedido (10.0%)', reward)

        # El combo agregado después la recibe al guardar.
        other = self._create_order()
        other_loose = self.lines.create({'order_id': other.id, 'product_id': self.suelto.id, 'product_uom_qty': 1.0})
        other_loose.action_set_line_promotion(reward.id)
        __, new_package, new_items = self._quote(other)
        self._assert_combo(new_package, new_items, 10.0, 'promo', 'Promo: Pedido (10.0%)', reward)

    def test_bulk_button_applies_to_combo_where_better(self):
        self._create_program(10.0, 'Esp Diez', self.package)
        order, package_line, items = self._quote()
        order.action_open_reward_wizard()
        self._assert_combo(package_line, items, 15.0, 'product', 'Desc. Producto (15.0%)')

        better = self._create_program(20.0, 'Esp Veinte', self.package)
        order.action_open_reward_wizard()
        self._assert_combo(package_line, items, 20.0, 'promo', 'Promo: Esp Veinte (20.0%)', better)

    def test_drop_combo_promo_restores_agreement(self):
        reward = self._create_program(10.0, 'Esp', self.package)
        __, package_line, items = self._quote()
        package_line.action_set_line_promotion(reward.id)
        package_line.action_set_line_promotion(False)
        self._assert_combo(package_line, items, 15.0, 'product', 'Desc. Producto (15.0%)')

    def test_drop_combo_promo_with_new_fixed_price(self):
        reward = self._create_program(10.0, 'Esp', self.package)
        order, package_line, items = self._quote(qty=3.0)
        package_line.action_set_line_promotion(reward.id)
        self.package_rule.write({'rule_type': 'fixed_price', 'fixed_price': 200.0, 'discount': 0.0})
        package_line.action_set_line_promotion(False)
        self.assertFalse(package_line.promo_reward_id)
        self.assertEqual(package_line.price_unit, 0.0)
        self.assertEqual(package_line.combo_package_price, 200.0)
        self.assertEqual(package_line.pricing_rule_type, 'fixed_price')
        self.assertEqual(items.mapped('discount'), [0.0] * 3)
        self.assertEqual(items.mapped('pricing_rule_type'), ['fixed_price'] * 3)
        self.assertAlmostEqual(sum(items.mapped('price_unit')), 200.0, places=2)
        self.assertAlmostEqual(order.amount_untaxed, 600.0, places=2)

    def test_combo_fixed_price_still_blocked(self):
        self.package_rule.write({'rule_type': 'fixed_price', 'fixed_price': 200.0, 'discount': 0.0})
        reward = self._create_program(10.0, 'Esp', self.package)
        order, package_line, __ = self._quote()
        self.assertEqual(package_line._get_line_promotion_block_reason(), 'Precio fijo de acuerdo')
        self.assertNotIn(reward, order._get_line_promotion_rewards()[package_line])

    def test_combo_promo_survives_save(self):
        reward = self._create_program(10.0, 'Esp', self.package)
        order, package_line, items = self._quote()
        package_line.action_set_line_promotion(reward.id)
        order.write({'order_line': [Command.create({'product_id': self.suelto.id, 'product_uom_qty': 1.0})]})
        self._assert_combo(package_line, items, 10.0, 'promo', 'Promo: Esp (10.0%)', reward)

    def test_combo_promo_minimum_not_self_cancelling(self):
        # Sin acuerdo, el combo vale 1,000: alcanza el mínimo de 950 solo si se mide sin su 10 %.
        self.package_rule.unlink()
        reward = self._create_program(10.0, 'Esp', self.package, minimum_amount=950.0)
        order, package_line, items = self._quote()
        package_line.action_set_line_promotion(reward.id)
        order.write({'order_line': [Command.create({'product_id': self.extra.id, 'product_uom_qty': 0.0})]})
        self._assert_combo(package_line, items, 10.0, 'promo', 'Promo: Esp (10.0%)', reward)

    def test_combo_order_promo_blocks_typed_discount(self):
        reward = self._create_program(10.0, 'Pedido')
        __, package_line, items = self._quote()
        package_line.action_set_line_promotion(reward.id)
        self.assertFalse(package_line.manual_discount_allowed)
        with self.assertRaises(UserError):
            package_line.discount = 5.0
        with self.assertRaises(ValidationError):
            items[0].discount = 5.0
        self._assert_combo(package_line, items, 10.0, 'promo', 'Promo: Pedido (10.0%)', reward)

    def test_combo_promo_hint_difference(self):
        # El paquete vale 0: la diferencia del selector sale del importe de sus productos.
        reward = self._create_program(10.0, 'Esp', self.package)
        __, package_line, __ = self._quote()
        option = package_line.promo_available_info['options'][0]
        self.assertEqual((option['reward_id'], option['difference']), (reward.id, -50.0))

        package_line.action_set_line_promotion(reward.id)
        package_line.invalidate_recordset(['promo_available_info'])
        self.assertEqual(package_line.promo_available_info['fallback']['difference'], 50.0)

        self.package_rule.write({'rule_type': 'fixed_price', 'fixed_price': 200.0, 'discount': 0.0})
        package_line.invalidate_recordset(['promo_available_info'])
        self.assertEqual(package_line.promo_available_info['fallback']['difference'], 700.0)


@tagged('post_install', '-at_install')
class TestSaleTintPromotion(TransactionCase):
    """La línea entintada admite promociones: el dominio se evalúa con la base, el % cubre el
    precio completo y al quitarla vuelve al descuento del Punto de Venta o a 0."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
            | cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
            | cls.env.ref('entintados_pdv.group_sale_edit_discount')
        )
        cls.env['loyalty.program'].search([]).active = False
        cls.lines = cls.env['sale.order.line']
        products = cls.env['product.product']
        base_type = cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        size = cls.env['tint.size'].search([('code', '=', 'G')], limit=1)
        cls.base = products.create({
            'name': 'Base Tinta Promo',
            'default_code': 'TEST-TINT-PROMO-BASE',
            'tint_role': 'base',
            'tint_base_type_id': base_type.id,
            'tint_size_id': size.id,
            'list_price': 200.0,
            'taxes_id': [Command.clear()],
            'is_storable': True,
            'invoice_policy': 'order',
        })
        cls.colorant = products.create({
            'name': 'Colorante Tinta Promo',
            'default_code': 'TEST-TINT-PROMO-RED',
            'tint_role': 'colorant',
            'uom_id': cls.env.ref('entintados_pdv.uom_tint_point').id,
            'list_price': 2.0,
            'taxes_id': [Command.clear()],
            'is_storable': True,
            'invoice_policy': 'order',
        })
        cls.plain = products.create({
            'name': 'Brocha Tinta Promo',
            'list_price': 100.0,
            'taxes_id': [Command.clear()],
            'invoice_policy': 'order',
        })
        color = cls.env['tint.color'].create({
            'name': 'Color Tinta Promo',
            'gallery_id': cls.env['tint.gallery'].search([], limit=1).id,
            'code_number': '9876',
        })
        cls.formula = cls.env['tint.color.formula'].create({
            'color_id': color.id,
            'base_type_id': base_type.id,
            'size_id': size.id,
            'line_ids': [Command.create({'colorant_id': cls.colorant.id, 'points': 12})],
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Tinta Promo',
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Tinta Promo',
            'is_customer': True,
            'phone': '1234567890',
        })
        cls.partner.property_product_pricelist = cls.pricelist

    @classmethod
    def _create_program(cls, discount, name, products=None, **rule):
        reward = {
            'reward_type': 'discount',
            'discount_mode': 'percent',
            'discount': discount,
            'discount_applicability': 'specific' if products else 'order',
            'required_points': 1,
        }
        if products:
            reward['discount_product_ids'] = [Command.set(products.ids)]
        return cls.env['loyalty.program'].create({
            'name': name,
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
                **rule,
            })],
            'reward_ids': [Command.clear(), Command.create(reward)],
        }).reward_ids

    def _create_order(self):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
        })

    def _tint(self, order, pos_discount=0.0):
        """Entintado del asistente o, con `pos_discount`, como llega de un ticket del PDV."""
        from ..models.sale_tint import _TINT_OPERATION
        line = order._apply_tint(self.formula, self.base, 1)
        if pos_discount:
            line.with_context(_tint_operation=_TINT_OPERATION).write(
                {'tint_snapshot': {**line.tint_snapshot, 'pos_discount': pos_discount}})
        return line

    def _plain(self, order):
        return self.lines.create({'order_id': order.id, 'product_id': self.plain.id, 'product_uom_qty': 1.0})

    def _assert_tint(self, line, discount, rule_type='none', origin=False, reward=None):
        self.assertEqual(
            (line.discount, line.technical_discount, line.pricing_rule_type, line.pricing_rule_origin),
            (discount, discount, rule_type, origin),
        )
        self.assertEqual(line.promo_reward_id, reward or self.env['loyalty.reward'])
        self.assertEqual(line.price_unit, 224.0)
        self.assertEqual(line.price_origin, 'tint')
        self.assertTrue(line.tint_snapshot)

    def test_specific_promo_on_tint_line(self):
        reward = self._create_program(10.0, 'Esp', self.base)
        order = self._create_order()
        line = self._tint(order)
        self._assert_tint(line, 0.0)
        self.assertTrue(line._admits_line_promotion())
        self.assertEqual(order._get_line_promotion_rewards()[line], reward)
        line.action_set_line_promotion(reward.id)
        self._assert_tint(line, 10.0, 'promo', 'Promo: Esp (10.0%)', reward)
        self.assertAlmostEqual(order.amount_untaxed, 201.6, places=2)

    def test_specific_promo_on_colorant_not_offered(self):
        reward = self._create_program(10.0, 'Esp Colorante', self.colorant)
        order = self._create_order()
        line = self._tint(order)
        self.assertTrue(line._admits_line_promotion())
        self.assertNotIn(reward, order._get_line_promotion_rewards()[line])
        with self.assertRaises(ValidationError):
            line._apply_line_promotion(reward)

    def test_order_promo_reaches_tint_line(self):
        reward = self._create_program(10.0, 'Pedido')
        order = self._create_order()
        tint_line = self._tint(order, pos_discount=5.0)
        self._plain(order).action_set_line_promotion(reward.id)
        self.assertEqual(order.order_promo_reward_id, reward)
        self._assert_tint(tint_line, 10.0, 'promo', 'Promo: Pedido (10.0%)', reward)

        # El entintado agregado después la recibe al guardar.
        other = self._create_order()
        other_plain = self._plain(other)
        other_plain.action_set_line_promotion(reward.id)
        new_tint = self._tint(other)
        other.write({'order_line': [Command.update(other_plain.id, {'product_uom_qty': 2.0})]})
        self._assert_tint(new_tint, 10.0, 'promo', 'Promo: Pedido (10.0%)', reward)

    def test_bulk_button_applies_to_tint_where_better(self):
        reward = self._create_program(3.0, 'Esp', self.base)
        order = self._create_order()
        wizard_line = self._tint(order)
        pos_line = self._tint(order, pos_discount=5.0)
        order.action_open_reward_wizard()
        self._assert_tint(wizard_line, 3.0, 'promo', 'Promo: Esp (3.0%)', reward)
        self._assert_tint(pos_line, 5.0)

    def test_drop_tint_promo_restores_pos_discount(self):
        reward = self._create_program(10.0, 'Esp', self.base)
        order = self._create_order()
        wizard_line = self._tint(order)
        pos_line = self._tint(order, pos_discount=5.0)
        for line in wizard_line | pos_line:
            line.action_set_line_promotion(reward.id)
            line.action_set_line_promotion(False)
        self._assert_tint(wizard_line, 0.0)
        self._assert_tint(pos_line, 5.0)

    def test_tint_promo_survives_update_prices(self):
        reward = self._create_program(10.0, 'Esp', self.base)
        order = self._create_order()
        line = self._tint(order)
        line.action_set_line_promotion(reward.id)
        order.action_update_prices()
        self._assert_tint(line, 10.0, 'promo', 'Promo: Esp (10.0%)', reward)

    def test_tint_promo_survives_partner_change(self):
        reward = self._create_program(10.0, 'Esp', self.base)
        order = self._create_order()
        line = self._tint(order, pos_discount=5.0)
        line.action_set_line_promotion(reward.id)
        order.partner_id = self.env['res.partner'].create({
            'name': 'Otro Cliente Tinta Promo',
            'is_customer': True,
            'phone': '1234567891',
            'property_product_pricelist': self.pricelist.id,
        })
        order._recompute_pricing_rules()
        self._assert_tint(line, 10.0, 'promo', 'Promo: Esp (10.0%)', reward)

    def test_tint_promo_minimum_not_self_cancelling(self):
        # El entintado vale 224: alcanza el mínimo de 220 solo si se mide sin su 10 %.
        reward = self._create_program(10.0, 'Esp', self.base, minimum_amount=220.0)
        order = self._create_order()
        line = self._tint(order)
        line.action_set_line_promotion(reward.id)
        order.write({'order_line': [Command.create({'product_id': self.plain.id, 'product_uom_qty': 0.0})]})
        self._assert_tint(line, 10.0, 'promo', 'Promo: Esp (10.0%)', reward)

    def test_tint_order_promo_blocks_typed_discount(self):
        reward = self._create_program(10.0, 'Pedido')
        order = self._create_order()
        line = self._tint(order)
        line.action_set_line_promotion(reward.id)
        self.assertFalse(line.manual_discount_allowed)
        with self.assertRaises(UserError):
            line.discount = 5.0
        self._assert_tint(line, 10.0, 'promo', 'Promo: Pedido (10.0%)', reward)

    def test_tint_direct_discount_write_still_rejected(self):
        reward = self._create_program(10.0, 'Esp', self.base)
        order = self._create_order()
        line = self._tint(order)
        with self.assertRaises(ValidationError):
            line.write({'discount': 20.0})
        line.action_set_line_promotion(reward.id)
        with self.assertRaises(ValidationError):
            line.write({'discount': 20.0})
        self._assert_tint(line, 10.0, 'promo', 'Promo: Esp (10.0%)', reward)

    def test_tint_promo_follows_reward_change(self):
        reward = self._create_program(10.0, 'Esp', self.base)
        order = self._create_order()
        line = self._tint(order)
        line.action_set_line_promotion(reward.id)
        reward.discount = 15.0
        order._update_programs_and_rewards()
        self._assert_tint(line, 15.0, 'promo', 'Promo: Esp (15.0%)', reward)

    def test_tint_promo_hint_fallback(self):
        # «Sin promoción» devuelve el entintado a su descuento del PDV o a 0, no a un acuerdo.
        reward = self._create_program(10.0, 'Esp', self.base)
        order = self._create_order()
        wizard_line = self._tint(order)
        pos_line = self._tint(order, pos_discount=5.0)
        for line, difference in ((wizard_line, -22.4), (pos_line, -11.2)):
            line.action_set_line_promotion(reward.id)
            line.invalidate_recordset(['promo_available_info'])
            self.assertAlmostEqual(line.promo_available_info['fallback']['difference'], difference, places=2)
