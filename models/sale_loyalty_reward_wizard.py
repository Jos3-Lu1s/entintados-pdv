# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SaleLoyaltyRewWiz(models.TransientModel):
    _inherit = 'sale.loyalty.reward.wizard'

    loyalty_action_type = fields.Selection(
        selection=[
            ('reward', 'Una Promoción'),
            ('discount', 'Descuento de Cliente'),
        ],
        string='¿Qué deseas aplicar?',
        required=True,
        default='reward',
    )
    partner_discount = fields.Float(
        compute='_compute_custom_discount',
        store=False,
        readonly=True,
    )

    def action_apply_custom(self):
        self.ensure_one()
        order = self.order_id or self.env['sale.order'].browse(self.env.context.get('active_id'))
        if not order:
            raise ValidationError(_("No se encontró un id de Orden de venta relacionado."))

        if self.loyalty_action_type == 'reward':
            for line in order.order_line:
                if line.display_type or getattr(line, 'is_reward_line', False):
                    continue
                if line.pricing_rule_type != 'fixed_price' and getattr(line.product_id, 'tint_role', False) != 'colorant':
                    line.discount = 0.0
                    line.pricing_rule_type = 'none'
                    line.pricing_rule_origin = ''
                    line.pricing_rule_id = False
            return super().action_apply()
        else:
            for line in order.order_line:
                if line.pricing_rule_type == 'promo':
                    line.discount = 0.0
                    line.pricing_rule_type = 'none'
                    line.pricing_rule_origin = ''
                    line.pricing_rule_id = False
            reward_lines = order.order_line.filtered(lambda l: getattr(l, 'is_reward_line', False) or l.reward_id)
            if reward_lines:
                reward_lines.unlink()
            order.with_context(force_price_recomputation=True)._recompute_pricing_rules()
            return True

    @api.depends('order_id.partner_id.discount')
    def _compute_custom_discount(self):
        for record in self:
            val = record.order_id.partner_id.discount or 0.0
            record.partner_discount = val * 100.0 if val <= 1.0 else val