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
    )
    partner_discount = fields.Float(
        compute='_compute_custom_discount',
        store=False,
        readonly=True,
    )

    def action_apply_custom(self):
        order_id = self.env.context.get('active_id')
        if order_id:
            order = self.env['sale.order'].browse(order_id)
        else:
            raise ValidationError(_("No se encontró un id de Orden de venta relacionado."))

        if self.loyalty_action_type == 'reward':
            res = super().action_apply()
            for line in order.order_line:
                if getattr(line, 'is_reward_line', False) or line.display_type:
                    continue
                rule = order.partner_id._get_partner_pricing_rule(line.product_id)
                if rule.get('type') == 'fixed_price':
                    line.price_unit = rule['price']
                    line.discount = 0.0
                else:
                    line.discount = 0.0
            return res
        else:
            for line in order.order_line:
                if getattr(line, 'is_reward_line', False) or line.display_type:
                    continue
                rule = order.partner_id._get_partner_pricing_rule(line.product_id)
                if rule.get('type') == 'fixed_price':
                    line.price_unit = rule['price']
                    line.discount = 0.0
                elif rule.get('type') == 'discount':
                    line.discount = rule['discount']
                else:
                    line.discount = 0.0

    @api.depends('order_id.partner_id.discount')
    def _compute_custom_discount(self):
        for record in self:
            val = record.order_id.partner_id.discount or 0.0
            record.partner_discount = val * 100