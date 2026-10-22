# -*- coding: utf-8 -*-

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestUntrackedDiscountAgreement(TransactionCase):
    """`discount` escrito sin el origen del acuerdo (importación, RPC o código): toma el acuerdo de
    descuento vigente si coincide con él y nunca cambia ni bloquea el precio."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
            | cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
            | cls.env.ref('entintados_pdv.group_sale_edit_discount')
        )
        cls.lines = cls.env['sale.order.line']
        cls.rules = cls.env['res.partner.discount.rule']
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Sin Origen -5',
            'item_ids': [(0, 0, {
                'applied_on': '3_global', 'compute_price': 'percentage', 'percent_price': 5.0,
            })],
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Pintura Sin Origen',
            'list_price': 100.0,
            'taxes_id': [(5, 0, 0)],
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Sin Origen',
            'is_customer': True,
            'phone': '1234567890',
        })
        cls.partner.property_product_pricelist = cls.pricelist

    def _discount_rule(self, discount):
        return self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.product.id,
            'discount': discount,
        })

    def _create_line(self):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'order_line': [(0, 0, {'product_id': self.product.id})],
        }).order_line

    def _badge(self, line):
        return line.pricing_rule_type, line.pricing_rule_origin

    def test_none_line_takes_new_agreement(self):
        for via in ('write', 'load'):
            with self.subTest(via=via):
                line = self._create_line()
                self.assertEqual(self._badge(line), ('none', ''))
                rule = self._discount_rule(10.0)
                try:
                    if via == 'write':
                        line.write({'discount': 10.0})
                    else:
                        result = self.lines.load(['.id', 'discount'], [[str(line.id), '10']])
                        self.assertFalse(result['messages'])
                        line.invalidate_recordset()

                    self.assertEqual(round(line.discount, 2), 10.0)
                    self.assertEqual(self._badge(line), ('product', 'Desc. Producto (10.0%)'))
                    self.assertEqual(round(line.technical_discount, 2), 10.0)

                    line.write({'product_uom_qty': 2.0})
                    self.assertEqual(round(line.discount, 2), 10.0)
                    self.assertEqual(line.price_subtotal, 171.0)
                finally:
                    # El subtest siguiente parte de una línea sin acuerdo.
                    rule.unlink()

    def test_agreement_change_updates_badge(self):
        rule = self._discount_rule(10.0)
        line = self._create_line()
        self.assertEqual(self._badge(line), ('product', 'Desc. Producto (10.0%)'))

        rule.write({'discount': 15.0})
        line.write({'discount': 15.0})
        self.assertEqual(round(line.discount, 2), 15.0)
        self.assertEqual(self._badge(line), ('product', 'Desc. Producto (15.0%)'))
        self.assertEqual(round(line.technical_discount, 2), 15.0)

    def test_fixed_price_agreement_keeps_discount_manual(self):
        rule = self._discount_rule(10.0)
        line = self._create_line()
        line.write({'discount': 7.0})
        self.assertEqual(self._badge(line), ('manual', 'Desc. Manual (7.0%)'))

        rule.write({'rule_type': 'fixed_price', 'fixed_price': 70.0})
        line.write({'discount': 0.0})
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(line.price_unit, 95.0)
        self.assertEqual(self._badge(line), ('manual', 'Desc. Manual (0.0%)'))

        line.write({'price_unit': 70.0})
        self.assertEqual(line.price_unit, 70.0)

    def _fixed_price_line_with_discount_agreement(self):
        """Línea guardada de precio fijo 70 cuyo acuerdo pasó después a descuento del 10 %."""
        rule = self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.product.id,
            'fixed_price': 70.0,
        })
        line = self._create_line()
        self._assert_fixed_price(line)
        rule.write({'rule_type': 'discount', 'discount': 10.0})
        return line

    def _assert_fixed_price(self, line):
        self.assertEqual(line.price_unit, 70.0)
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(self._badge(line), ('fixed_price', 'Precio Fijo'))

    def test_fixed_price_line_rejects_matching_discount(self):
        line = self._fixed_price_line_with_discount_agreement()
        with self.assertRaises(ValidationError):
            line.write({'discount': 10.0})
        line.invalidate_recordset()
        self._assert_fixed_price(line)

    def test_fixed_price_line_zero_discount_stays_fixed(self):
        line = self._fixed_price_line_with_discount_agreement()
        line.write({'discount': 0.0})
        self._assert_fixed_price(line)
        self.assertFalse(line.manual_discount_allowed)
        with self.assertRaises(ValidationError):
            line.write({'discount': 15.0})

    def test_untracked_discount_skips_non_eligible_lines(self):
        self.partner.discount = 0.10
        order = self._create_line().order_id
        order.action_confirm()
        self.env['sale.advance.payment.inv'].create({
            'sale_order_ids': [Command.set(order.ids)],
            'advance_payment_method': 'fixed',
            'fixed_amount': 50.0,
        }).create_invoices()
        down_payment = order.order_line.filtered(lambda line: line.is_downpayment and not line.display_type)
        discount_product = self.env['sale.order.discount'].create({
            'sale_order_id': order.id,
        })._get_discount_product()
        global_discount = self.lines.create({
            'order_id': order.id,
            'product_id': discount_product.id,
            'name': 'Descuento',
            'price_unit': -20.0,
            'technical_price_unit': 0,
            'product_uom_qty': 1.0,
            'tax_ids': [Command.clear()],
            'extra_tax_data': {'computation_key': f'global_discount,{order.id}'},
            'sequence': 999,
        })

        down_payment.write({'discount': 0.0})
        self.assertEqual(self._badge(down_payment), ('none', ''))
        self.assertFalse(down_payment.pricing_rule_id)

        global_discount.write({'discount': 10.0})
        self.assertEqual(self._badge(global_discount), ('none', ''))
        self.assertFalse(global_discount.pricing_rule_id)
        self.assertEqual(global_discount.price_unit, -20.0)

    def test_fixed_price_line_product_change_with_discount(self):
        """Cambio de una línea de precio fijo a un producto con acuerdo de descuento, con `discount`
        en el mismo write: el descuento es del producto nuevo."""
        self._discount_rule(10.0)
        fixed_product = self.env['product.product'].create({
            'name': 'Pintura Sin Origen Fija',
            'list_price': 100.0,
            'taxes_id': [(5, 0, 0)],
        })
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': fixed_product.id,
            'fixed_price': 70.0,
        })
        expected = {
            4.0: ('manual', 'Desc. Manual (4.0%)'),
            10.0: ('product', 'Desc. Producto (10.0%)'),
            0.0: ('manual', 'Desc. Manual (0.0%)'),
        }
        for via in ('write', 'load'):
            for discount, badge in expected.items():
                with self.subTest(via=via, discount=discount):
                    line = self.env['sale.order'].create({
                        'partner_id': self.partner.id,
                        'pricelist_id': self.pricelist.id,
                        'order_line': [(0, 0, {'product_id': fixed_product.id})],
                    }).order_line
                    self._assert_fixed_price(line)
                    if via == 'write':
                        line.write({'product_id': self.product.id, 'discount': discount})
                    else:
                        result = self.lines.load(
                            ['.id', 'product_id/.id', 'discount'],
                            [[str(line.id), str(self.product.id), str(discount)]],
                        )
                        self.assertFalse(result['messages'])
                        line.invalidate_recordset()

                    self.assertEqual(line.product_id, self.product)
                    self.assertEqual(round(line.discount, 2), discount)
                    self.assertEqual(self._badge(line), badge)
                    self.assertEqual(line.price_unit, 95.0)

    def test_removed_agreement_clears_badge(self):
        rule = self._discount_rule(10.0)
        line = self._create_line()
        self.assertEqual(self._badge(line), ('product', 'Desc. Producto (10.0%)'))

        rule.unlink()
        line.write({'discount': 0.0})
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(self._badge(line), ('none', ''))
