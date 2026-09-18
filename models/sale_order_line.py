# -*- coding: utf-8 -*-

from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.depends(
        'product_id',
        'product_uom_id',
        'product_uom_qty',
        'order_id.partner_id',
        'order_id.partner_id.discount_rule_ids',
        'order_id.pricelist_id',
    )
    def _compute_price_unit(self):
        super()._compute_price_unit()
        for line in self:
            if (
                line.product_id
                and not line.display_type
                and line.order_id.partner_id
                and not getattr(line, 'is_reward_line', False)
            ):
                rule = line.order_id.partner_id._get_partner_pricing_rule(line.product_id)
                if rule.get('type') == 'fixed_price':
                    line.price_unit = rule['price']
                    line.technical_price_unit = rule['price']

    def _reset_price_unit(self):
        super()._reset_price_unit()
        for line in self:
            if (
                line.product_id
                and not line.display_type
                and line.order_id.partner_id
                and not getattr(line, 'is_reward_line', False)
            ):
                rule = line.order_id.partner_id._get_partner_pricing_rule(line.product_id)
                if rule.get('type') == 'fixed_price':
                    line.price_unit = rule['price']
                    line.technical_price_unit = rule['price']

    @api.depends(
        'product_id',
        'product_uom_id',
        'product_uom_qty',
        'order_id.partner_id',
        'order_id.partner_id.discount',
        'order_id.partner_id.discount_rule_ids',
    )
    def _compute_discount(self):
        super()._compute_discount()
        for line in self:
            if (
                line.product_id
                and not line.display_type
                and line.order_id.partner_id
                and line.product_template_id.type != 'service'
                and not getattr(line, 'is_reward_line', False)
            ):
                rule = line.order_id.partner_id._get_partner_pricing_rule(line.product_id)
                if rule.get('type') == 'fixed_price':
                    line.discount = 0.0
                elif rule.get('type') == 'discount':
                    line.discount = rule['discount']
                else:
                    line.discount = 0.0