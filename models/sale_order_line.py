# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare

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
            ('manual', 'Descuento Manual'),
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
    price_origin = fields.Selection(
        selection=[
            ('list', 'Precio de venta'),
            ('pricelist', 'Tarifa'),
            ('fixed_price', 'Precio fijo de acuerdo'),
            ('manual', 'Manual'),
        ],
        string='Origen del precio',
        compute='_compute_price_unit',
        store=True,
        precompute=True,
        readonly=False,
        copy=False,
    )
    price_origin_label = fields.Char(
        string='Detalle origen del precio',
        compute='_compute_price_unit',
        store=True,
        precompute=True,
        readonly=False,
        copy=False,
    )
    # Último descuento asignado por el cálculo. El onchange de `discount` también se dispara
    # cuando lo cambia el cálculo: solo es manual si difiere de este valor.
    technical_discount = fields.Float(
        digits='Discount',
        compute='_compute_discount',
        store=True,
        precompute=True,
        readonly=False,
        copy=False,
    )
    manual_discount_allowed = fields.Boolean(compute='_compute_manual_discount_allowed')

    @api.depends('display_type', 'pricing_rule_type', 'product_id', 'is_reward_line')
    def _compute_manual_discount_allowed(self):
        for line in self:
            line.manual_discount_allowed = line._is_manual_discount_allowed()

    def _is_manual_discount_allowed(self):
        """Línea que admite descuento manual: no es precio fijo de acuerdo, recompensa, producto
        de descuento de la compañía ni sección/nota."""
        self.ensure_one()
        return not (
            self.display_type
            or self.pricing_rule_type == 'fixed_price'
            or getattr(self, 'is_reward_line', False)
            or (self.product_id and self.product_id == self.company_id.sale_discount_product_id)
        )

    @api.constrains('discount', 'pricing_rule_type')
    def _check_manual_discount(self):
        for line in self:
            if line.pricing_rule_type == 'fixed_price' and line.discount:
                raise ValidationError(_(
                    "La línea «%(line)s» tiene precio fijo de acuerdo: no admite descuento.",
                    line=line.name,
                ))
            if line.pricing_rule_type == 'manual' and not 0.0 <= line.discount <= 100.0:
                raise ValidationError(_(
                    "El descuento de la línea «%(line)s» debe estar entre 0 y 100 %%.",
                    line=line.name,
                ))

    def _manual_discount_values(self, discount):
        return {
            'technical_discount': discount,
            'pricing_rule_type': 'manual',
            'pricing_rule_origin': f"Desc. Manual ({discount:.1f}%)",
            'pricing_rule_id': False,
        }

    def _manual_price_values(self):
        return {'price_origin': 'manual', 'price_origin_label': 'Modificado de forma manual'}

    def _keeps_manual_price(self):
        """Precio manual que sobrevive a cantidad/UdM, "Actualizar precios" y cambio de cliente.

        Se pierde al cambiar de producto (`reset_manual_pricing`).
        """
        self.ensure_one()
        return bool(
            self.price_origin == 'manual'
            and not self.env.context.get('reset_manual_pricing')
            and (not self._origin.id or self.product_id == self._origin.product_id)
        )

    def _set_price_origin(self):
        """Origen de un precio recién calculado con la tarifa: regla aplicada o precio de venta."""
        for line in self:
            pricelist = line.order_id.pricelist_id
            item = self.env['product.pricelist.item']
            if line.product_id and pricelist:
                item = item.browse(pricelist._get_product_rule(
                    product=line.product_id, **line._get_pricelist_kwargs()))
            if not line.product_id or line.display_type:
                line.price_origin = False
                line.price_origin_label = False
            elif item:
                line.price_origin = 'pricelist'
                line.price_origin_label = f"Tarifa: {pricelist.name} — {item.name}: {item.price}"
            else:
                line.price_origin = 'list'
                line.price_origin_label = 'Precio de venta del producto'

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
                        vals['technical_discount'] = 0.0
                        vals['pricing_rule_type'] = 'fixed_price'
                        vals['pricing_rule_origin'] = rule.get('origin_label') or 'Precio Fijo'
                        vals['pricing_rule_id'] = rule.get('rule') and rule['rule'].id or False
                        vals['price_origin'] = 'fixed_price'
                        vals['price_origin_label'] = vals['pricing_rule_origin']
                    else:
                        # Descuento o sin acuerdo: tras el write, el compute forzado resuelve el
                        # acuerdo del producto nuevo (sin forzar se quedaría el congelado).
                        recompute_lines |= line
        # Descuento escrito por código, RPC o importación: sustituye al del acuerdo y queda manual.
        manual_discount = (
            'discount' in vals
            and not self.env.context.get('skip_manual_discount_breakdown')
            and 'pricing_rule_type' not in vals
        )
        # Precio tecleado (no recalculado): `technical_price_unit` no acompaña al nuevo precio.
        manual_price = (
            'price_unit' in vals
            and 'product_id' not in vals
            and 'price_origin' not in vals
            and not self.env.context.get('sale_write_from_compute')
            and not self.env.context.get('skip_manual_discount_breakdown')
            and vals.get('technical_price_unit') != vals['price_unit']
        )
        manual_price_lines = self.browse()
        if manual_price:
            fixed_lines = self.filtered(lambda line: line.pricing_rule_type == 'fixed_price')
            for line in fixed_lines:
                if line.currency_id.compare_amounts(line.price_unit, vals['price_unit']):
                    raise ValidationError(_(
                        "La línea «%(line)s» tiene precio fijo de acuerdo: no se puede cambiar su precio.",
                        line=line.name,
                    ))
            manual_price_lines = self - fixed_lines
        res = super().write(vals)
        if manual_price_lines:
            manual_price_lines.with_context(skip_manual_discount_breakdown=True).write(
                self._manual_price_values())
        if manual_discount:
            self.with_context(skip_manual_discount_breakdown=True).write(
                self._manual_discount_values(vals['discount'] or 0.0))
        elif recompute_lines:
            # Cambio de producto: se pierden precio y descuento manuales.
            recompute_lines = recompute_lines.with_context(
                force_price_recomputation=True, reset_manual_pricing=True,
            )
            recompute_lines._compute_price_unit()
            recompute_lines._compute_discount()
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

    @api.onchange('discount')
    def _onchange_discount_manual(self):
        digits = self.env['decimal.precision'].precision_get('Discount')
        for line in self:
            if float_compare(line.discount, line.technical_discount, precision_digits=digits):
                line.update(line._manual_discount_values(line.discount))

    @api.onchange('price_unit')
    def _onchange_price_unit_manual(self):
        # Solo si difiere del último precio calculado: el onchange también se dispara por el cálculo.
        for line in self:
            if line.pricing_rule_type != 'fixed_price' and line._has_core_manual_price():
                line.update(line._manual_price_values())

    @api.onchange('product_id')
    def _onchange_product_id(self):
        super()._onchange_product_id()
        for line in self:
            if not line.product_id:
                continue
            line = line.with_context(force_price_recomputation=True, reset_manual_pricing=True)
            line._reset_price_unit()
            line._compute_discount()

    @api.depends(
        'product_id',
        'product_uom_id',
        'product_uom_qty',
        'order_id.pricelist_id',
    )
    def _compute_price_unit(self):
        force_recompute = self.env.context.get('force_price_recomputation')
        # Líneas que el core no recalcula: conservan su origen tal cual.
        previous = {line: (line.price_origin, line.price_origin_label) for line in self}
        core_skipped = {
            line for line in self
            if not line.order_id
            or line.is_downpayment
            or line._is_global_discount()
            or line.qty_invoiced > 0
            or (line.product_id.expense_policy == 'cost' and line.is_expense)
            or (not force_recompute and line._has_core_manual_price())
        }
        super()._compute_price_unit()
        for line in self:
            if line in core_skipped:
                line.price_origin, line.price_origin_label = previous[line]
                continue
            if not force_recompute and (line._keeps_saved_pricing() or line._keeps_manual_price()):
                continue
            if (
                line.product_id
                and not line.display_type
                and line.order_id.partner_id
                and not getattr(line, 'is_reward_line', False)
            ):
                rule = line.order_id.partner_id._get_partner_pricing_rule(line.product_id)
                if rule.get('type') == 'fixed_price':
                    line._apply_fixed_price_agreement(rule)

    def _has_core_manual_price(self):
        """Mismo criterio que el core: `price_unit` distinto del último precio calculado."""
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id or self.env.company.currency_id
        return bool(currency.compare_amounts(self.technical_price_unit, self.price_unit))

    def _get_display_price_ignore_combo(self):
        # La tarifa siempre va dentro de `price_unit`, también las reglas de porcentaje: `discount`
        # queda para el acuerdo o el descuento manual.
        self.ensure_one()
        return self._get_pricelist_price()

    def _reset_price_unit(self):
        force_recompute = self.env.context.get('force_price_recomputation')
        for line in self.with_context(sale_write_from_compute=True):
            if line._keeps_manual_price() and not (force_recompute and line._has_fixed_price_agreement()):
                # Precio manual: no se toca; el precio fijo de acuerdo sí gana sobre él.
                continue
            if not force_recompute and line._keeps_saved_pricing():
                # Línea guardada: la tarifa se recalcula (cantidad/UdM), pero un precio fijo
                # de acuerdo ya aplicado se conserva congelado.
                frozen_type = line._origin.pricing_rule_type
                frozen_price = line._origin.price_unit
                super(SaleOrderLine, line)._reset_price_unit()
                if frozen_type == 'fixed_price':
                    line.price_unit = frozen_price
                    line.technical_price_unit = frozen_price
                    line.price_origin = 'fixed_price'
                    line.price_origin_label = line._origin.pricing_rule_origin or 'Precio Fijo'
                else:
                    line._set_price_origin()
                continue
            super(SaleOrderLine, line)._reset_price_unit()
            rule = {}
            if (
                line.product_id
                and not line.display_type
                and line.order_id.partner_id
                and not getattr(line, 'is_reward_line', False)
            ):
                rule = line.order_id.partner_id._get_partner_pricing_rule(line.product_id)
            if rule.get('type') == 'fixed_price':
                line._apply_fixed_price_agreement(rule)
            else:
                line._set_price_origin()

    @api.depends(
        'product_id',
        'product_uom_id',
        'product_uom_qty',
        'order_id.pricelist_id',
    )
    def _compute_discount(self):
        # El acuerdo congelado se lee antes del super(): el core reescribe `discount`.
        force_recompute = self.env.context.get('force_price_recomputation')
        frozen = {
            line: (
                line._origin.discount,
                line._origin.pricing_rule_type or 'none',
                line._origin.pricing_rule_origin or '',
                line._origin.pricing_rule_id,
            )
            for line in self
            if not force_recompute and line._keeps_saved_pricing()
        }
        # Descuento manual: sobrevive a cantidad/UdM, "Actualizar precios" y cambio de cliente;
        # se pierde al cambiar de producto (`reset_manual_pricing`) o ante un precio fijo.
        reset_manual = self.env.context.get('reset_manual_pricing')
        manual = {
            line: (line.discount, line.pricing_rule_origin)
            for line in self
            if line.pricing_rule_type == 'manual'
            and not reset_manual
            and (not line._origin.id or line.product_id == line._origin.product_id)
        }
        # El % que deja el core es el de la tarifa, que ya va en el precio: se descarta siempre.
        # Las líneas de precio fijo no pasan por el core: llamado directo sobre líneas guardadas
        # (p. ej. `_recompute_prices`), su % sería un write real y la restricción lo rechazaría.
        fixed_lines = self.filtered(lambda line: line.pricing_rule_type == 'fixed_price')
        super(SaleOrderLine, self - fixed_lines)._compute_discount()
        # Llamado directo sobre líneas guardadas (p. ej. `_recompute_prices`), asignar `discount`
        # pasa por write(): no es un descuento manual.
        for line in self.with_context(skip_manual_discount_breakdown=True):
            if line in manual and (line in frozen or not line._has_fixed_price_agreement()):
                discount, origin = manual[line]
                badge = ('manual', origin, False)
            elif line in frozen:
                discount, rule_type, origin, rule_id = frozen[line]
                if rule_type not in AGREEMENT_DISCOUNT_TYPES:
                    discount = 0.0
                badge = (rule_type, origin, rule_id)
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
                rule_id = rule.get('rule') and rule['rule'].id or False
                if rule.get('type') == 'fixed_price':
                    discount = 0.0
                    badge = ('fixed_price', rule.get('origin_label') or 'Precio Fijo', rule_id)
                elif rule.get('type') == 'discount':
                    discount = rule['discount']
                    badge = (rule.get('origin_type') or 'product', rule.get('origin_label') or '', rule_id)
                else:
                    discount = 0.0
                    badge = ('none', '', False)
            # En una línea guardada cada asignación es un write y la restricción de precio fijo
            # vería el valor intermedio: hacia precio fijo, `discount` (= 0) antes que el badge;
            # en otro caso, el badge antes que `discount`, para no dejar precio fijo con descuento.
            if badge[0] == 'fixed_price':
                line.discount = discount
                line.technical_discount = discount
                line.pricing_rule_type, line.pricing_rule_origin, line.pricing_rule_id = badge
            else:
                line.pricing_rule_type, line.pricing_rule_origin, line.pricing_rule_id = badge
                line.discount = discount
                line.technical_discount = discount

    def _apply_fixed_price_agreement(self, rule):
        """Precio fijo de acuerdo: gana sobre la tarifa y sobre cualquier descuento o precio manual.

        `discount` va primero: en una línea guardada cada asignación es un write y la restricción
        de precio fijo vería el descuento anterior.
        """
        self.ensure_one()
        line = self.with_context(skip_manual_discount_breakdown=True)
        line.discount = 0.0
        line.technical_discount = 0.0
        line.price_unit = rule['price']
        line.technical_price_unit = rule['price']
        line.pricing_rule_type = 'fixed_price'
        line.pricing_rule_origin = rule.get('origin_label') or 'Precio Fijo'
        line.pricing_rule_id = rule.get('rule') and rule['rule'].id or False
        line.price_origin = 'fixed_price'
        line.price_origin_label = line.pricing_rule_origin

    def _has_fixed_price_agreement(self):
        self.ensure_one()
        if (
            not self.product_id
            or self.display_type
            or not self.order_id.partner_id
            or getattr(self, 'is_reward_line', False)
        ):
            return False
        rule = self.order_id.partner_id._get_partner_pricing_rule(self.product_id)
        return rule.get('type') == 'fixed_price'
