# -*- coding: utf-8 -*-

from odoo import Command
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLoyaltyProgramUnlink(TransactionCase):
    """Borrar un programa que ya se usó lo archiva con sus recompensas: los pedidos conservan
    la promoción con la que se cobraron."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente borrado de programas',
            'is_customer': True,
            'phone': '5551234567',
        })
        cls.service = cls.env['product.product'].create({
            'name': 'Servicio borrado de programas',
            'type': 'service',
            'list_price': 100.0,
            'taxes_id': [Command.clear()],
        })
        cls.config = cls.env['pos.config'].create({'name': 'Caja borrado de programas'})
        cls.session = cls.env['pos.session'].create({
            'config_id': cls.config.id,
            'user_id': cls.env.uid,
        })

    def _program(self):
        program = self.env['loyalty.program'].create({
            'name': 'Promoción para borrar',
            'program_type': 'promotion',
            'applies_on': 'current',
            'trigger': 'auto',
            'rule_ids': [Command.create({'minimum_amount': 0})],
            'reward_ids': [Command.create({
                'reward_type': 'discount',
                'discount': 20.0,
                'discount_mode': 'percent',
                'discount_applicability': 'order',
            })],
        })
        return program, program.reward_ids

    def _delete(self, program):
        # El core solo deja borrar programas archivados.
        program.action_archive()
        program.unlink()

    def _quotation(self, reward):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [Command.create({'product_id': self.service.id, 'product_uom_qty': 1})],
        })
        order.order_line._write_line_promotion(reward)
        return order

    def test_program_used_in_pos_is_archived(self):
        program, reward = self._program()
        pos_order = self.env['pos.order'].create({
            'session_id': self.session.id,
            'partner_id': self.partner.id,
            'amount_total': 80.0,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'order_promo_reward_id': reward.id,
            'lines': [Command.create({
                'product_id': self.service.id,
                'qty': 1,
                'price_unit': 100.0,
                'discount': 20.0,
                'price_subtotal': 80.0,
                'price_subtotal_incl': 80.0,
                'pricing_rule_type': 'promo',
                'promo_reward_id': reward.id,
            })],
        })
        self._delete(program)

        self.assertTrue(program.exists())
        self.assertFalse(program.active)
        self.assertTrue(reward.exists())
        self.assertFalse(reward.active)
        self.assertEqual(pos_order.lines.promo_reward_id, reward)
        self.assertEqual(pos_order.order_promo_reward_id, reward)

    def test_program_used_in_confirmed_sale_is_archived(self):
        program, reward = self._program()
        order = self._quotation(reward)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self._delete(program)

        self.assertTrue(program.exists())
        self.assertFalse(program.active)
        self.assertFalse(reward.active)
        self.assertEqual(order.order_line.promo_reward_id, reward)

    def test_program_used_only_in_open_quotation_is_deleted(self):
        program, reward = self._program()
        order = self._quotation(reward)
        self._delete(program)

        self.assertFalse(program.exists())
        self.assertFalse(order.order_line.promo_reward_id)
        self.assertNotEqual(order.order_line.pricing_rule_type, 'promo')

    def test_unused_program_is_deleted(self):
        program, reward = self._program()
        self._delete(program)

        self.assertFalse(program.exists())
        self.assertFalse(reward.exists())
