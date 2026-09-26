# -*- coding: utf-8 -*-

from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    pricing_rule_type = fields.Selection(
        selection=[
            ('none', 'Sin acuerdo'),
            ('fixed_price', 'Precio Fijo'),
            ('product', 'Descuento por Producto'),
            ('line', 'Descuento por Línea'),
            ('scheme', 'Descuento por Esquema'),
            ('global', 'Descuento Global'),
            ('promo', 'Promoción'),
        ],
        string='Tipo de Acuerdo',
        compute='_compute_discount',
        store=True,
        precompute=True,
        copy=False,
        readonly=False,
    )
    pricing_rule_origin = fields.Char(
        string='Origen Acuerdo',
        compute='_compute_discount',
        store=True,
        precompute=True,
        readonly=False,
        copy=False,
    )
    pricing_rule_id = fields.Many2one(
        comodel_name='res.partner.discount.rule',
        string='Regla de Acuerdo',
        compute='_compute_discount',
        store=True,
        precompute=True,
        readonly=False,
        copy=False,
        ondelete='set null',
    )

    def write(self, vals):
        if 'product_id' in vals and not self.env.context.get('skip_pricing_rule_update'):
            for line in self:
                product = self.env['product.product'].browse(vals['product_id']) if vals.get('product_id') else line.product_id
                if product and product.type != 'service' and line.order_id.partner_id:
                    rule = line.order_id.partner_id._get_partner_pricing_rule(product)
                    if rule.get('type') == 'fixed_price':
                        vals.setdefault('price_unit', rule['price'])
                        vals['technical_price_unit'] = rule['price']
                        vals['discount'] = 0.0
                        vals['pricing_rule_type'] = 'fixed_price'
                        vals['pricing_rule_origin'] = rule.get('origin_label') or 'Precio Fijo'
                        vals['pricing_rule_id'] = rule.get('rule') and rule['rule'].id or False
                    elif rule.get('type') == 'discount':
                        vals['discount'] = rule['discount']
                        vals['pricing_rule_type'] = rule.get('origin_type') or 'product'
                        vals['pricing_rule_origin'] = rule.get('origin_label') or ''
                        vals['pricing_rule_id'] = rule.get('rule') and rule['rule'].id or False
                    else:
                        vals['discount'] = 0.0
                        vals['pricing_rule_type'] = 'none'
                        vals['pricing_rule_origin'] = ''
                        vals['pricing_rule_id'] = False
        return super().write(vals)

    def _keeps_saved_pricing(self):
        """Línea guardada cuyo producto y cliente no cambiaron: conserva el acuerdo de `_origin`.

        En un onchange con el cliente cambiado y sin guardar, `_origin` aún trae los valores
        del cliente anterior, así que no puede usarse como acuerdo vigente.
        """
        self.ensure_one()
        return bool(
            self._origin.id
            and self.product_id == self._origin.product_id
            and self.order_id.partner_id == self._origin.order_id.partner_id
        )

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
            if not force_recompute and line._keeps_saved_pricing():
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
                    line.pricing_rule_type = 'fixed_price'
                    line.pricing_rule_origin = rule.get('origin_label') or 'Precio Fijo'
                    line.pricing_rule_id = rule.get('rule') and rule['rule'].id or False

    def _reset_price_unit(self):
        force_recompute = self.env.context.get('force_price_recomputation')
        for line in self:
            if not force_recompute and line._keeps_saved_pricing():
                # Línea guardada: la tarifa se recalcula (cantidad/UdM), pero un precio fijo
                # de acuerdo ya aplicado se conserva congelado.
                frozen_type = line._origin.pricing_rule_type
                frozen_price = line._origin.price_unit
                super(SaleOrderLine, line)._reset_price_unit()
                if frozen_type == 'fixed_price':
                    line.price_unit = frozen_price
                    line.technical_price_unit = frozen_price
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
                    line.pricing_rule_type = 'fixed_price'
                    line.pricing_rule_origin = rule.get('origin_label') or 'Precio Fijo'
                    line.pricing_rule_id = rule.get('rule') and rule['rule'].id or False

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
            if not force_recompute and line._keeps_saved_pricing():
                line.discount = line._origin.discount
                line.pricing_rule_type = line._origin.pricing_rule_type or 'none'
                line.pricing_rule_origin = line._origin.pricing_rule_origin or ''
                line.pricing_rule_id = line._origin.pricing_rule_id or False
                continue
            if getattr(line, 'is_reward_line', False):
                line.pricing_rule_type = 'none'
                line.pricing_rule_origin = ''
                line.pricing_rule_id = False
                continue
            if (
                line.product_id
                and not line.display_type
                and line.order_id.partner_id
                and line.product_id.type != 'service'
            ):
                rule = line.order_id.partner_id._get_partner_pricing_rule(line.product_id)
                if rule.get('type') == 'fixed_price':
                    line.discount = 0.0
                    line.pricing_rule_type = 'fixed_price'
                    line.pricing_rule_origin = rule.get('origin_label') or 'Precio Fijo'
                    line.pricing_rule_id = rule.get('rule') and rule['rule'].id or False
                elif rule.get('type') == 'discount':
                    line.discount = rule['discount']
                    line.pricing_rule_type = rule.get('origin_type') or 'product'
                    line.pricing_rule_origin = rule.get('origin_label') or ''
                    line.pricing_rule_id = rule.get('rule') and rule['rule'].id or False
                else:
                    line.discount = 0.0
                    line.pricing_rule_type = 'none'
                    line.pricing_rule_origin = ''
                    line.pricing_rule_id = False
            else:
                line.discount = 0.0
                line.pricing_rule_type = 'none'
                line.pricing_rule_origin = ''
                line.pricing_rule_id = False