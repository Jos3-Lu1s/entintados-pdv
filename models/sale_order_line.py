# -*- coding: utf-8 -*-

from odoo import api, fields, models
from odoo.tools import SQL, float_round
from odoo.tools.sql import column_exists, create_column

AGREEMENT_DISCOUNT_TYPES = ('product', 'line', 'scheme', 'global')


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
    pricelist_discount = fields.Float(
        string='Desc. Tarifa (%)',
        digits='Discount',
        compute='_compute_discount',
        store=True,
        precompute=True,
        readonly=False,
        copy=False,
    )
    agreement_discount = fields.Float(
        string='Desc. Acuerdo (%)',
        digits='Discount',
        compute='_compute_discount',
        store=True,
        precompute=True,
        readonly=False,
        copy=False,
    )

    def _auto_init(self):
        # Columnas creadas y rellenadas antes del super(): si las crea el ORM, el compute se
        # dispararía sobre todas las líneas existentes y podría cambiar importes históricos.
        cr = self.env.cr
        if not column_exists(cr, 'sale_order_line', 'pricelist_discount'):
            create_column(cr, 'sale_order_line', 'pricelist_discount', 'numeric')
            cr.execute("UPDATE sale_order_line SET pricelist_discount = 0")
        if not column_exists(cr, 'sale_order_line', 'agreement_discount'):
            create_column(cr, 'sale_order_line', 'agreement_discount', 'numeric')
            if column_exists(cr, 'sale_order_line', 'pricing_rule_type'):
                cr.execute(SQL(
                    """UPDATE sale_order_line
                          SET agreement_discount = CASE WHEN pricing_rule_type IN %s
                                                        THEN COALESCE(discount, 0) ELSE 0 END""",
                    AGREEMENT_DISCOUNT_TYPES,
                ))
            else:
                # Instalación limpia: `pricing_rule_type` aún no existe, no hay acuerdos previos.
                cr.execute("UPDATE sale_order_line SET agreement_discount = 0")
        return super()._auto_init()

    def write(self, vals):
        recompute_lines = self.browse()
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
                    else:
                        # Descuento o sin acuerdo: tras el write, el compute forzado resuelve el
                        # acuerdo del producto nuevo (sin forzar se quedaría el congelado).
                        recompute_lines |= line
        # Descuento escrito por código, RPC o importación: manda el valor escrito.
        manual_discount = (
            'discount' in vals
            and not self.env.context.get('skip_manual_discount_breakdown')
            and not {'pricelist_discount', 'agreement_discount', 'pricing_rule_type'} & vals.keys()
        )
        res = super().write(vals)
        if manual_discount:
            self.with_context(skip_manual_discount_breakdown=True).write({
                'pricelist_discount': 0.0,
                'agreement_discount': 0.0,
                'pricing_rule_type': 'none',
                'pricing_rule_origin': '',
                'pricing_rule_id': False,
            })
        elif recompute_lines:
            recompute_lines.with_context(force_price_recomputation=True)._compute_discount()
        return res

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
        # El acuerdo congelado se lee antes del super(): se toma del campo almacenado
        # `agreement_discount`, que el core nunca escribe, y no de `_origin.discount`.
        force_recompute = self.env.context.get('force_price_recomputation')
        frozen = {
            line: (
                line._origin.agreement_discount,
                line._origin.pricing_rule_type or 'none',
                line._origin.pricing_rule_origin or '',
                line._origin.pricing_rule_id,
            )
            for line in self
            if not force_recompute and line._keeps_saved_pricing()
        }
        super()._compute_discount()
        discount_enabled = self.env['product.pricelist.item']._is_discount_feature_enabled()
        # Llamado directo sobre líneas guardadas (p. ej. `_recompute_prices`), asignar `discount`
        # pasa por write(): no es un descuento manual.
        for line in self.with_context(skip_manual_discount_breakdown=True):
            # % de la tarifa: el que dejó el core, solo si pudo calcularlo.
            pricelist_discount = 0.0
            if (
                line.product_id
                and not line.display_type
                and line.order_id.pricelist_id
                and discount_enabled
                and not line.combo_item_id
            ):
                pricelist_discount = line.discount

            if line in frozen:
                agreement_discount, rule_type, origin, rule_id = frozen[line]
                if rule_type not in AGREEMENT_DISCOUNT_TYPES:
                    agreement_discount = 0.0
                if rule_type == 'fixed_price':
                    pricelist_discount = 0.0
                line.pricing_rule_type = rule_type
                line.pricing_rule_origin = origin
                line.pricing_rule_id = rule_id
            else:
                rule = {}
                if (
                    line.product_id
                    and not line.display_type
                    and line.order_id.partner_id
                    and line.product_id.type != 'service'
                    and not getattr(line, 'is_reward_line', False)
                ):
                    rule = line.order_id.partner_id._get_partner_pricing_rule(line.product_id)
                if rule.get('type') == 'fixed_price':
                    pricelist_discount = agreement_discount = 0.0
                    line.pricing_rule_type = 'fixed_price'
                    line.pricing_rule_origin = rule.get('origin_label') or 'Precio Fijo'
                    line.pricing_rule_id = rule.get('rule') and rule['rule'].id or False
                elif rule.get('type') == 'discount':
                    agreement_discount = rule['discount']
                    line.pricing_rule_type = rule.get('origin_type') or 'product'
                    line.pricing_rule_origin = rule.get('origin_label') or ''
                    line.pricing_rule_id = rule.get('rule') and rule['rule'].id or False
                else:
                    agreement_discount = 0.0
                    line.pricing_rule_type = 'none'
                    line.pricing_rule_origin = ''
                    line.pricing_rule_id = False

            line.pricelist_discount = pricelist_discount
            line.agreement_discount = agreement_discount
            line.discount = line._combine_discounts(pricelist_discount, agreement_discount)

    def _combine_discounts(self, pricelist_discount, agreement_discount):
        """Descuento en cascada: el acuerdo se aplica sobre el precio ya rebajado por la tarifa."""
        combined = 100.0 * (1.0 - (1.0 - pricelist_discount / 100.0) * (1.0 - agreement_discount / 100.0))
        digits = self.env['decimal.precision'].precision_get('Discount')
        return float_round(combined, precision_digits=digits)