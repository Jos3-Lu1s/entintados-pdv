# -*- coding: utf-8 -*-

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.depends(
        'product_id',
        'product_uom_id',
        'product_uom_qty',
        'order_id.partner_id',
        'order_id.partner_id.discount',
    )
    def _compute_discount(self):
        super()._compute_discount()
        for line in self:
            if (
                line.product_id
                and not line.display_type
                and line.order_id.partner_id
                and line.product_template_id.type != 'service'
                and line.product_template_id.list_price != 0
                and line.product_template_id.standard_price != 0
            ):
                line.discount = line.order_id.partner_id.discount*100
    def write(self, vals):
        # Detecta cuándo el motor de loyalty invalida un reward
        # (reset_loyalty(complete=True) hace write({'reward_id': False, ...}))
        orders_to_check = self.env['sale.order']
        if 'reward_id' in vals and not vals.get('reward_id'):
            orders_to_check = self.filtered('reward_id').mapped('order_id')

        res = super().write(vals)

        for order in orders_to_check:
            order._reapply_partner_discount_if_no_reward()
        return res

    def unlink(self):
        orders_to_check = self.filtered('is_reward_line').mapped('order_id')
        res = super().unlink()
        for order in orders_to_check:
            order._reapply_partner_discount_if_no_reward()
        return res