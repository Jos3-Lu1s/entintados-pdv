# -*- coding: utf-8 -*-

from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.onchange('product_id')
    def _onchange_product_id(self):
        super()._onchange_product_id()
        for line in self:
            if not line.product_id:
                continue
            line.with_context(force_price_recomputation=True)._reset_price_unit()
            line.with_context(force_price_recomputation=True)._compute_discount()

    @api.depends(
        'product_id',
        'product_uom_id',
        'product_uom_qty',
        'order_id.pricelist_id',
    )
    def _compute_price_unit(self):
        super()._compute_price_unit()
        force_recompute = self.env.context.get('force_price_recomputation')
        for line in self:
            if line.order_id.state in ('sale', 'done', 'cancel'):
                continue
            if line._origin.id and not force_recompute:
                continue
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
        force_recompute = self.env.context.get('force_price_recomputation')
        for line in self:
            if line.order_id.state in ('sale', 'done', 'cancel'):
                continue
            if line._origin.id and not force_recompute:
                continue
            super(SaleOrderLine, line)._reset_price_unit()
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
        'order_id.pricelist_id',
    )
    def _compute_discount(self):
        super()._compute_discount()
        force_recompute = self.env.context.get('force_price_recomputation')
        for line in self:
            if line.order_id.state in ('sale', 'done', 'cancel'):
                continue
            if line._origin.id and not force_recompute:
                line.discount = line._origin.discount
                continue
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