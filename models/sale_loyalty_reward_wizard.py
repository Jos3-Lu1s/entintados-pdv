# -*- coding: utf-8 -*-

import re
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
class SaleLoyaltyRewWiz(models.TransientModel):
    _inherit = 'sale.loyalty.reward.wizard'

    loyalty_action_type = fields.Selection(
            selection=[
                ('reward', 'Una Promoción'),
                ('discount', 'Descuento de Cliente'),
            ],
            string='¿Qué deseas aplicar?',
            required=True,
        )

    partner_discount= fields.Float(
        compute='_compute_custom_discount',
        store=False,
        readonly=True
    )
    @api.depends('order_id.partner_id.discount')
    def _compute_custom_discount(self):
        for wiz in self:
            val = wiz.order_id.partner_id.discount or 0.0
            wiz.partner_discount = val * 100

    # --- Filtro de promociones por producto en la orden ---

    @api.depends('order_id')
    def _compute_claimable_reward_ids(self):
        super()._compute_claimable_reward_ids()
        for wizard in self:
            wizard.reward_ids = wizard._filter_rewards_by_order_lines(wizard.reward_ids)

    def _filter_rewards_by_order_lines(self, rewards):
        """Deja solo las recompensas cuyo producto/categoría restringida
        está presente en las líneas actuales de la orden."""
        self.ensure_one()
        order_products = self.order_id.order_line.filtered(
            lambda l: not l.is_reward_line
        ).mapped('product_id')

        if not order_products:
            return self.env['loyalty.reward']

        valid_rewards = self.env['loyalty.reward']
        for reward in rewards:
            if reward.reward_type == 'discount' and reward.discount_applicability == 'specific':
                domain = Domain(reward._get_discount_product_domain()) & Domain(
                    [('id', 'in', order_products.ids)]
                )
                if self.env['product.product'].search_count(domain, limit=1):
                    valid_rewards |= reward
                # si no hay match, se descarta: no se muestra la promoción
            else:
                # 'order', 'cheapest', o reward_type == 'product' (regalo)
                # se consideran válidas sin restricción de producto específico
                valid_rewards |= reward
        return valid_rewards

    # --- Aplicar promoción o descuento ---

    def action_apply_custom(self):
        self.ensure_one()
        order = self.order_id
        if not order:
            raise ValidationError(_("No se encontró la Orden de venta relacionada."))

        if self.loyalty_action_type == 'reward':
            # Limpiar el descuento manual ANTES de aplicar la promoción,
            # para que el cálculo del reward use el price_total correcto (sin descuento previo).
            order.order_line.filtered(
                lambda l: not l.is_reward_line
            ).write({'discount': 0})

            res = super().action_apply()
            return res

        elif self.loyalty_action_type == 'discount':
            discount = (order.partner_id.discount or 0.0) * 100
            order.order_line.filtered(
                lambda l: not l.is_reward_line
            ).write({'discount': discount})
            reward_lines = order.order_line.filtered(lambda l: l.is_reward_line)
            if reward_lines:
                reward_lines.unlink()
            return {'type': 'ir.actions.act_window_close'}

    