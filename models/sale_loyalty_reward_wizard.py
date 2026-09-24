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

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order_id = res.get('order_id') or self.env.context.get('default_order_id') or self.env.context.get('active_id')
        if order_id:
            order = self.env['sale.order'].browse(order_id)
            has_active_promo = any(
                l.pricing_rule_type == 'promo' or getattr(l, 'is_reward_line', False)
                for l in order.order_line
            )
            if 'loyalty_action_type' in fields_list and 'default_loyalty_action_type' not in self.env.context:
                res['loyalty_action_type'] = 'discount' if has_active_promo else 'reward'
        return res

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
            return super(SaleLoyaltyRewWiz, self.with_context(include_applied_rewards=True)).action_apply()
        else:
            reward_lines = order.order_line.filtered(
                lambda l: getattr(l, 'is_reward_line', False) or l.reward_id or l.reward_identifier_code == 'gift_product'
            )
            for rline in reward_lines:
                base_line = order.order_line.filtered(
                    lambda l: l != rline
                    and l.product_id == rline.product_id
                    and not getattr(l, 'is_reward_line', False)
                    and not l.reward_id
                    and l.reward_identifier_code != 'gift_product'
                )[:1]
                if base_line:
                    base_line.product_uom_qty += rline.product_uom_qty
            if reward_lines:
                reward_lines.unlink()

            for line in order.order_line:
                if line.pricing_rule_type == 'promo':
                    line.discount = 0.0
                    line.pricing_rule_type = 'none'
                    line.pricing_rule_origin = ''
                    line.pricing_rule_id = False

            order.with_context(force_price_recomputation=True)._recompute_pricing_rules()
            order._update_programs_and_rewards()
            return True

    @api.depends('order_id.partner_id.discount', 'order_id.partner_id.discount_rule_ids')
    def _compute_custom_discount(self):
        for record in self:
            val = record.order_id.partner_id.discount or 0.0
            if not val and record.order_id:
                rule_discounts = [
                    rule.discount for rule in record.order_id.partner_id.discount_rule_ids
                    if rule.rule_type == 'discount' and rule.discount
                ]
                if rule_discounts:
                    val = max(rule_discounts)
            record.partner_discount = val * 100.0 if 0 < val <= 1.0 else val