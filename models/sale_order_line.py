# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, format_amount

AGREEMENT_DISCOUNT_TYPES = ('product', 'line', 'scheme', 'global')
# Campos del acuerdo que los productos de un combo copian de su paquete.
COMBO_AGREEMENT_FIELDS = {
    'product_id', 'discount', 'technical_discount',
    'pricing_rule_type', 'pricing_rule_origin', 'pricing_rule_id',
}


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
    promo_reward_id = fields.Many2one(
        comodel_name='loyalty.reward',
        string='Promoción aplicada',
        copy=False,
        ondelete='set null',
    )
    promo_available_info = fields.Json(string='Promociones disponibles', compute='_compute_promo_available')
    promo_available_count = fields.Integer(string='Número de promociones disponibles', compute='_compute_promo_available')
    price_origin = fields.Selection(
        selection=[
            ('list', 'Precio de venta'),
            ('pricelist', 'Tarifa'),
            ('fixed_price', 'Precio fijo de acuerdo'),
            ('manual', 'Manual'),
            ('combo', 'Parte de combo'),
            ('tint', 'Entintado'),
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
    # Números con los que se calculó el precio de un producto del combo: los del catálogo
    # pueden cambiar después y el desglose dejaría de cuadrar con `price_unit`.
    combo_price_snapshot = fields.Json(
        string='Desglose del precio del combo',
        compute='_compute_price_unit',
        store=True,
        precompute=True,
        readonly=True,
        copy=True,
    )
    # Línea del paquete: precio que se reparte entre sus productos (tarifa o precio fijo de acuerdo),
    # en la UdM y la moneda de la línea. Guardado para congelar el precio fijo como en el resto.
    # Sin redondear, como `technical_price_unit`: un cambio de moneda de ida y vuelta vuelve exacto.
    combo_package_price = fields.Float(
        string='Precio repartido del combo',
        compute='_compute_price_unit',
        store=True,
        precompute=True,
        readonly=True,
        copy=False,
    )
    # Línea del paquete: suma de sus productos, el subtotal que la lista de escritorio muestra en
    # la fila del combo. La tarjeta móvil lo muestra en lugar de `price_subtotal`, que vale 0.
    combo_subtotal = fields.Monetary(
        string='Subtotal del combo',
        compute='_compute_combo_subtotal',
        currency_field='currency_id',
    )
    # Línea del paquete: suma de sus productos, que son los que llevan el importe real.
    discount_amount = fields.Monetary(
        string='Descuento $',
        compute='_compute_discount_amount',
        currency_field='currency_id',
    )
    sale_tax_amount_detail = fields.Char(string='Importe por impuesto', compute='_compute_sale_tax_amount_detail')
    sale_amount_before_discount = fields.Monetary(
        string='Importe antes de descuento', compute='_compute_discount_amount', currency_field='currency_id',
    )

    @api.depends('tax_ids', 'tax_ids.amount', 'price_unit', 'discount',
                 'product_uom_qty', 'currency_id', 'company_id', 'product_id',
                 'order_id.partner_shipping_id', 'display_type')
    @api.depends_context('lang')
    def _compute_sale_tax_amount_detail(self):
        for line in self:
            line.sale_tax_amount_detail = False
            if line.display_type or not line.tax_ids or not line.currency_id:
                continue
            taxes = line.tax_ids.with_company(line.company_id).compute_all(
                line.price_unit * (1 - line.discount / 100), line.currency_id,
                line.product_uom_qty, product=line.product_id, partner=line.order_id.partner_shipping_id,
            )['taxes']
            line.sale_tax_amount_detail = ' · '.join(
                format_amount(line.env, tax['amount'], line.currency_id)
                for tax in taxes
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

    @api.depends(
        'display_type', 'pricing_rule_type', 'product_id', 'is_reward_line', 'is_downpayment',
        'combo_item_id', 'order_id.order_promo_reward_id',
    )
    def _compute_manual_discount_allowed(self):
        for line in self:
            line.manual_discount_allowed = line._is_manual_discount_allowed()

    @api.depends(
        'product_type', 'virtual_id', 'order_id.order_line.price_subtotal',
        'order_id.order_line.linked_line_id', 'order_id.order_line.linked_virtual_id',
    )
    def _compute_combo_subtotal(self):
        for line in self:
            line.combo_subtotal = line.product_type == 'combo' and sum(
                line._get_linked_lines().filtered('combo_item_id').mapped('price_subtotal')
            )

    @api.depends(
        'product_type', 'virtual_id', 'currency_id', 'price_subtotal', 'price_unit',
        'product_uom_qty', 'discount', 'tax_ids', 'order_id.partner_shipping_id',
        'order_id.order_line.tax_ids', 'order_id.order_line.price_subtotal',
        'order_id.order_line.price_unit', 'order_id.order_line.product_uom_qty',
        'order_id.order_line.discount', 'order_id.order_line.linked_line_id',
        'order_id.order_line.linked_virtual_id',
    )
    def _compute_discount_amount(self):
        # Los productos de un combo que también se calculan aquí no repiten su cálculo de impuestos.
        own = {line: line._get_own_discount_amount() for line in self if line.product_type != 'combo'}
        for line in self:
            if line.product_type == 'combo':
                items = line._get_linked_lines().filtered('combo_item_id')
                line.discount_amount = sum(
                    own[item] if item in own else item._get_own_discount_amount() for item in items
                )
                subtotal = sum(items.mapped('price_subtotal'))
            else:
                line.discount_amount = own[line]
                subtotal = line.price_subtotal
            line.sale_amount_before_discount = subtotal + line.discount_amount

    def _get_own_discount_amount(self):
        self.ensure_one()
        if self.display_type or not self.product_uom_qty or not self.price_unit or not self.discount:
            return 0.0
        base = self._get_amount_without_discount()
        amount = base - self.price_subtotal
        if self.discount > 0 and base >= 0:
            amount = max(0.0, amount)
        return self.currency_id.round(amount) if self.currency_id else amount

    @api.depends('product_id')
    def _compute_product_uom_readonly(self):
        # El paquete combo solo se vende en la UdM base de su producto (`_check_combo_package_uom`).
        super()._compute_product_uom_readonly()
        self.filtered(lambda line: line.product_type == 'combo').product_uom_readonly = True

    def _is_manual_discount_allowed(self):
        """Línea que admite descuento manual: no es precio fijo de acuerdo, recompensa, producto
        de descuento de la compañía, anticipo, producto de combo ni sección/nota.

        El anticipo es un importe ya cobrado: su descuento pasa a la deducción de la factura final.
        Un producto de combo copia el descuento de su paquete, donde se captura.
        """
        self.ensure_one()
        return not (
            self.display_type
            or self.is_downpayment
            or self.combo_item_id
            or self.pricing_rule_type == 'fixed_price'
            or self.is_reward_line
            or (self.product_id and self.product_id == self.company_id.sale_discount_product_id)
            or self._has_order_promotion_lock()
        )

    def _has_order_promotion_lock(self):
        """Línea que lleva la promoción de pedido: su descuento no se cambia mientras esté activa."""
        self.ensure_one()
        return bool(self.order_id.order_promo_reward_id and self._admits_line_promotion())

    def _order_promotion_lock_error(self):
        return UserError(_(
            "El pedido tiene la promoción %(program)s en todas sus líneas. Quítala del pedido "
            "para cambiar el descuento.",
            program=self.order_id.order_promo_reward_id.program_id.name,
        ))

    @api.constrains('discount', 'pricing_rule_type', 'is_downpayment')
    def _check_manual_discount(self):
        for line in self:
            if line.is_downpayment and line.discount:
                raise ValidationError(_(
                    "La línea «%(line)s» es un anticipo: no admite descuento.",
                    line=line.name,
                ))
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

    # `state` hace que reabrir o confirmar un pedido revise también las líneas
    # cuyo producto se reclasificó como colorante después de capturarlas.
    @api.constrains('product_id', 'state')
    def _check_colorant_not_sold_alone(self):
        for line in self:
            if line.state != 'cancel' and line.product_id.tint_role == 'colorant':
                raise ValidationError(_(
                    "El colorante «%s» no se vende solo: solo puede salir "
                    "como parte de un entintado en Ventas o en el Punto de Venta.",
                    line.product_id.display_name,
                ))

    @api.constrains('product_id', 'product_uom_id')
    def _check_combo_package_uom(self):
        # El núcleo solo copia la cantidad del paquete a sus productos, no la UdM.
        for line in self.filtered(lambda l: l.product_type == 'combo'):
            if line.product_uom_id != line.product_id.uom_id:
                raise ValidationError(_(
                    "El combo «%(product)s» solo se vende en su unidad de medida «%(uom)s».",
                    product=line.product_id.display_name,
                    uom=line.product_id.uom_id.display_name,
                ))

    @api.constrains('product_id', 'price_unit')
    def _check_combo_package_price(self):
        # El precio del paquete vive en `combo_package_price` y lo reparten sus productos.
        for line in self.filtered(lambda l: l.product_type == 'combo'):
            if not line.currency_id.is_zero(line.price_unit):
                raise ValidationError(_(
                    "El combo «%(product)s» no lleva precio propio: su precio lo reparten sus productos.",
                    product=line.product_id.display_name,
                ))

    @api.constrains('product_id', 'product_uom_id', 'product_uom_qty', 'linked_line_id')
    def _check_combo_item_line(self):
        # La lista de escritorio no deja cambiar la UdM ni la cantidad de un producto del combo:
        # la cantidad es la del paquete, que el núcleo y `write` le copian.
        precision = self.env['decimal.precision'].precision_get('Product Unit')
        for line in self.filtered('combo_item_id'):
            package = line._get_linked_line()
            if line.product_uom_id != line.product_id.uom_id:
                raise ValidationError(_(
                    "«%(product)s» es parte del combo «%(package)s»: solo se vende en su unidad "
                    "de medida «%(uom)s».",
                    product=line.product_id.display_name,
                    package=package.product_id.display_name,
                    uom=line.product_id.uom_id.display_name,
                ))
            if package and float_compare(
                line.product_uom_qty, package.product_uom_qty, precision_digits=precision,
            ):
                raise ValidationError(_(
                    "«%(product)s» es parte del combo «%(package)s»: su cantidad es la del combo.",
                    product=line.product_id.display_name,
                    package=package.product_id.display_name,
                ))

    def _manual_discount_values(self, discount):
        return {
            'technical_discount': discount,
            'pricing_rule_type': 'manual',
            'pricing_rule_origin': f"Desc. Manual ({discount:.1f}%)",
            'pricing_rule_id': False,
            'promo_reward_id': False,
        }

    def _manual_price_values(self):
        return {'price_origin': 'manual', 'price_origin_label': 'Modificado de forma manual'}

    def _keeps_manual_values(self):
        """Lo tecleado en la línea (precio, descuento o promoción) sobrevive a cantidad/UdM,
        "Actualizar precios" y cambio de cliente; se pierde al cambiar de producto
        (`reset_manual_pricing`).
        """
        self.ensure_one()
        return bool(
            not self.env.context.get('reset_manual_pricing')
            and (not self._origin.id or self.product_id == self._origin.product_id)
        )

    def _keeps_manual_price(self):
        self.ensure_one()
        return self.price_origin == 'manual' and self._keeps_manual_values()

    def _set_price_origin(self):
        """Origen de un precio recién calculado con la tarifa: regla aplicada o precio de venta."""
        for line in self:
            if not line.product_id or line.display_type:
                line.price_origin = False
                line.price_origin_label = False
                continue
            combo_line = line.combo_item_id and line._get_linked_line()
            if combo_line:
                # La tarifa se aplica al paquete, no a este producto.
                line.price_origin = 'combo'
                line.price_origin_label = (
                    f"Parte del combo «{combo_line.product_id.name}»: {line.combo_item_id.combo_id.name}"
                )
                continue
            pricelist = line.order_id.pricelist_id
            item = self.env['product.pricelist.item']
            if pricelist:
                item = item.browse(pricelist._get_product_rule(
                    product=line.product_id, **line._get_pricelist_kwargs()))
            if item:
                line.price_origin = 'pricelist'
                line.price_origin_label = f"Tarifa: {pricelist.name} — {item.name}: {item.price}"
            else:
                line.price_origin = 'list'
                line.price_origin_label = 'Precio de venta del producto'

    def _is_untracked_discount_vals(self, vals):
        """`discount` sin el origen del acuerdo: código, RPC, importación o una vista sin esos campos."""
        return (
            'discount' in vals
            and 'pricing_rule_type' not in vals
            and 'technical_discount' not in vals
            and not self.env.context.get('skip_manual_discount_breakdown')
        )

    def _agreement_discount(self, product=None):
        """% que el acuerdo vigente da a la línea, como en `_compute_discount`; 0 si no hay.

        `None` con precio fijo: su acuerdo es un precio, no un % con el que comparar un descuento.
        """
        self.ensure_one()
        rule = self._get_agreement_rule(product)
        if rule.get('type') == 'fixed_price':
            return None
        return rule['discount'] if rule.get('type') == 'discount' else 0.0

    def _is_manual_discount_vals(self, vals):
        """Descuento escrito por código, RPC o importación: sustituye al del acuerdo y queda manual.

        Se evalúa sobre líneas ya existentes: sin `technical_discount`, un `discount` igual al del
        acuerdo vigente viene del acuerdo.
        """
        if (
            'discount' not in vals
            or 'pricing_rule_type' in vals
            or self.env.context.get('skip_manual_discount_breakdown')
        ):
            return False
        digits = self.env['decimal.precision'].precision_get('Discount')
        discount = vals['discount'] or 0.0
        if 'technical_discount' in vals:
            # El formulario no envía `pricing_rule_type` si no cambió (otro cliente con acuerdo del
            # mismo tipo): un `discount` igual a su `technical_discount` viene del cálculo.
            return bool(float_compare(vals['technical_discount'] or 0.0, discount, precision_digits=digits))
        product = self.env['product.product'].browse(vals['product_id']) if vals.get('product_id') else None
        for line in self:
            agreement = line._agreement_discount(product)
            if agreement is None or float_compare(agreement, discount, precision_digits=digits):
                return True
        return False

    @api.model_create_multi
    def create(self, vals_list):
        # El cálculo previo a la creación pone el badge del acuerdo junto al `discount` recibido.
        # Se copian los valores: el acuerdo vigente solo se resuelve con la línea ya creada.
        sent_vals = [dict(vals) for vals in vals_list]
        lines = self._create_linked_lines_last(vals_list)
        for line, vals in zip(lines, sent_vals):
            # Un `discount = 0` importado sobre un precio fijo o un anticipo pasa la restricción:
            # marcarlo manual desbloquearía el descuento de esa línea.
            if line._is_manual_discount_vals(vals) and line._is_manual_discount_allowed():
                line.with_context(skip_manual_discount_breakdown=True).write(
                    line._manual_discount_values(vals['discount'] or 0.0))
        return lines

    def _create_linked_lines_last(self, vals_list):
        """Crea antes las líneas a las que otras del mismo lote apuntan por `linked_virtual_id`.

        `_get_linked_line` busca esa línea en `order_id.order_line`: en un solo lote todavía no
        existe y el cálculo previo de un producto de combo (precio, descuento) revienta. El cliente
        web no envía esos valores cuando son de solo lectura, así que el cálculo sí corre.
        """
        virtual_ids = {vals['virtual_id'] for vals in vals_list if vals.get('virtual_id')}
        linked = {
            index for index, vals in enumerate(vals_list)
            if vals.get('linked_virtual_id') in virtual_ids
        }
        if not linked:
            return super().create(vals_list)
        created = {}
        for batch in (
            [index for index in range(len(vals_list)) if index not in linked],
            sorted(linked),
        ):
            if batch:
                records = super().create([vals_list[index] for index in batch])
                created.update(zip(batch, records))
        return self.browse([created[index].id for index in range(len(vals_list))])

    def _get_product_catalog_lines_data(self, **kwargs):
        # El core solo bloquea varias líneas del mismo producto: una recompensa suelta sería editable.
        res = super()._get_product_catalog_lines_data(**kwargs)
        if len(self) == 1 and self.is_reward_line:
            res['readOnly'] = True
        return res

    def write(self, vals):
        if (
            'discount' in vals
            and not self.env.context.get('skip_manual_discount_breakdown')
            and not self.env.context.get('force_price_recomputation')
        ):
            digits = self.env['decimal.precision'].precision_get('Discount')
            for line in self:
                if (
                    float_compare(line.discount, vals['discount'] or 0.0, precision_digits=digits)
                    and line._has_order_promotion_lock()
                ):
                    raise line._order_promotion_lock_error()
        if 'product_id' in vals:
            # El configurador del núcleo borra y vuelve a crear los productos del combo: un cambio
            # de producto por `write` los separaría de lo que eligió el configurador.
            for line in self.filtered('combo_item_id'):
                if line.product_id.id != vals['product_id']:
                    raise ValidationError(_(
                        "«%(product)s» es parte del combo «%(package)s»: para cambiarlo, vuelve "
                        "a configurar el combo.",
                        product=line.product_id.display_name,
                        package=line._get_linked_line().product_id.display_name,
                    ))
        self._check_combo_pricing_vals(vals)
        update_pricing_rule ='product_id' in vals and not self.env.context.get('skip_pricing_rule_update')
        if update_pricing_rule and len(self) > 1:
            # El acuerdo depende del cliente de cada línea: un solo `vals` llevaría la regla y el
            # precio técnico de la última línea a todas.
            for line in self:
                line.write(vals)
            return True
        # Se inyectan valores del acuerdo: no deben volver al llamador, que puede reutilizar `vals`.
        vals = dict(vals)
        new_product = self.env['product.product'].browse(vals['product_id']) if vals.get('product_id') else None
        split_price_lines = self.browse()
        if (
            'price_unit' in vals
            and 'product_id' in vals
            and not self.env.context.get('sale_write_from_compute')
            and not self.env.context.get('skip_manual_discount_breakdown')
        ):
            split_price_lines = self.filtered('combo_item_id')
        if (
            'price_unit' not in vals
            and 'product_id' not in vals
            and not self.env.context.get('skip_manual_discount_breakdown')
        ):
            # El formulario fuerza el envío del origen aunque `price_unit` no viaje (precio
            # bloqueado): escribirlo protegería del recálculo a todo `_compute_price_unit` y el
            # precio de la tarifa no se guardaría.
            vals.pop('price_origin', None)
            vals.pop('price_origin_label', None)
        recompute_lines = self.browse()
        # Precio fijo en la UdM del producto y la moneda de la compañía, por línea: se convierte a
        # su UdM y moneda finales tras el write.
        fixed_price_by_line = {}
        explicit_price = 'price_unit' in vals
        if update_pricing_rule:
            for line in self:
                product = new_product or line.product_id
                if line._admits_partner_agreement(product):
                    rule = line.order_id.partner_id._get_partner_pricing_rule(product)
                    # El precio fijo de un paquete no va en su precio: lo reparten sus productos.
                    if rule.get('type') == 'fixed_price' and product.type != 'combo':
                        if not explicit_price:
                            fixed_price_by_line[line] = rule['price']
                        _discount, (rule_type, origin, rule_id) = line._agreement_discount_badge(rule)
                        vals.setdefault('price_unit', rule['price'])
                        vals['technical_price_unit'] = rule['price']
                        vals['discount'] = 0.0
                        vals['technical_discount'] = 0.0
                        vals['pricing_rule_type'] = rule_type
                        vals['pricing_rule_origin'] = origin
                        vals['pricing_rule_id'] = rule_id
                        vals['price_origin'] = 'fixed_price'
                        vals['price_origin_label'] = origin
                    else:
                        # Descuento o sin acuerdo: tras el write, el compute forzado resuelve el
                        # acuerdo del producto nuevo (sin forzar se quedaría el congelado).
                        recompute_lines |= line
        manual_discount_lines = self.filtered(lambda line: line._is_manual_discount_vals(vals))
        # Línea a la que se le escribe el % del acuerdo vigente: toma el badge de ese acuerdo, sea
        # cual sea el guardado. Precio fijo y promoción por línea conservan el suyo.
        # En un recálculo forzado el `discount` lo escribe el propio `_compute_discount` del core.
        agreement_discount_lines = self.browse()
        if self._is_untracked_discount_vals(vals) and not self.env.context.get('force_price_recomputation'):
            agreement_discount_lines = (self - manual_discount_lines).filtered(
                lambda line: line.pricing_rule_type != 'fixed_price'
                and line._admits_partner_agreement(new_product)
            )
        # Precio tecleado (no recalculado): `technical_price_unit` no acompaña al nuevo precio.
        manual_price = (
            'price_unit' in vals
            and 'product_id' not in vals
            and 'price_origin' not in vals
            and not self.env.context.get('sale_write_from_compute')
            and not self.env.context.get('skip_manual_discount_breakdown')
        )
        if manual_price and 'technical_price_unit' in vals:
            # Se compara con la precisión de la moneda, como `_has_core_manual_price`: `price_unit`
            # llega redondeado a sus dígitos y `technical_price_unit` no (una tarifa de -10 % sobre
            # 12.25 da 11.03 frente a 11.025), y esa diferencia no es un precio tecleado.
            manual_price = any(
                line._get_price_currency().compare_amounts(vals['technical_price_unit'], vals['price_unit'])
                for line in self
            )
        # Cambio de UdM sin precio (código, RPC, importación o el formulario, donde `price_unit` es de
        # solo lectura en precio fijo): tras el write `_origin` ya trae la UdM nueva, así que el
        # precio fijo se convierte aquí desde la UdM anterior. Se parte de `technical_price_unit`, sin
        # redondear, como el onchange: tras un cambio de moneda `price_unit` ya perdió decimales.
        fixed_uom = {}
        if 'product_uom_id' in vals and 'price_unit' not in vals and 'product_id' not in vals:
            fixed_uom = {
                line: (line.product_uom_id, line.technical_price_unit)
                for line in self
                if line.pricing_rule_type == 'fixed_price' and not line.qty_invoiced
            }
        manual_price_lines = self.browse()
        if manual_price:
            fixed_lines = self.filtered(lambda line: line.pricing_rule_type == 'fixed_price')
            for line in fixed_lines:
                if line.currency_id.compare_amounts(line.price_unit, vals['price_unit']):
                    raise ValidationError(_(
                        "La línea «%(line)s» tiene precio fijo de acuerdo: no se puede cambiar su precio.",
                        line=line.name,
                    ))
            # El core escribe el importe del anticipo al publicar su factura: no es un precio tecleado.
            # Reescribir el precio que ya tiene la línea (p. ej. reimportar una exportación) tampoco,
            # ni el que pone `sale_loyalty` a sus recompensas.
            manual_price_lines = (
                self - fixed_lines - self.filtered('is_downpayment') - self.filtered('reward_id')
            ).filtered(
                lambda line: line._get_price_currency().compare_amounts(line.price_unit, vals['price_unit'])
            )
        if (
            'discount' in vals
            and 'pricing_rule_type' not in vals
            and any(line.pricing_rule_type == 'fixed_price' for line in recompute_lines)
        ):
            # El producto nuevo no tiene precio fijo: sin esto la restricción vería el precio fijo
            # del producto anterior junto al `discount` recibido. El recálculo o el descuento manual
            # de después ponen el badge definitivo.
            vals['pricing_rule_type'] = 'none'
        # El formulario envía el origen nuevo, no la referencia a la recompensa.
        if vals.get('pricing_rule_type', 'promo') != 'promo':
            vals.setdefault('promo_reward_id', False)
        res = super().write(vals)
        if 'product_uom_qty' in vals:
            # El núcleo solo copia la cantidad del paquete a sus productos en el onchange del
            # formulario: por código, RPC o el catálogo se quedarían con la anterior.
            for package in self.filtered(lambda line: line.product_type == 'combo'):
                package.linked_line_ids.filtered(
                    lambda line: line.combo_item_id and line.product_uom_qty != package.product_uom_qty
                ).write({'product_uom_qty': package.product_uom_qty})
        for line, base_price in fixed_price_by_line.items():
            price = line._fixed_price_in_line_units(base_price)
            if line.currency_id.compare_amounts(line.price_unit, price):
                line.with_context(skip_manual_discount_breakdown=True).write({
                    'price_unit': price,
                    'technical_price_unit': price,
                })
        for line, (old_uom, old_price) in fixed_uom.items():
            if line.product_uom_id == old_uom:
                continue
            price = old_uom._compute_price(old_price, line.product_uom_id)
            line.with_context(skip_manual_discount_breakdown=True).write({
                'price_unit': price,
                'technical_price_unit': price,
            })
        if manual_price_lines:
            manual_price_lines.with_context(skip_manual_discount_breakdown=True).write(
                self._manual_price_values())
        if agreement_discount_lines:
            agreement_discount_lines.with_context(
                force_price_recomputation=True, reset_manual_pricing=True,
            )._compute_discount()
        # Un `discount = 0` importado sobre un precio fijo o un anticipo pasa la restricción:
        # marcarlo manual desbloquearía el descuento de esa línea.
        manual_discount_lines = manual_discount_lines.filtered(lambda line: line._is_manual_discount_allowed())
        if manual_discount_lines:
            manual_discount_lines.with_context(
                skip_manual_discount_breakdown=True,
            ).write(self._manual_discount_values(vals['discount'] or 0.0))
        elif recompute_lines:
            # Cambio de producto: se pierden precio y descuento manuales.
            recompute_lines = recompute_lines.with_context(
                force_price_recomputation=True, reset_manual_pricing=True,
            )
            recompute_lines._compute_price_unit()
            recompute_lines._compute_discount()
        if vals.keys() & COMBO_AGREEMENT_FIELDS:
            packages = self.filtered(lambda line: line.product_type == 'combo')
            packages.linked_line_ids.filtered('combo_item_id')._sync_combo_package_agreement()
        for line in split_price_lines:
            # Con `product_id` el precio recibido no se revisa antes: se guarda el reparto del
            # paquete y solo se acepta si coincide con lo recibido.
            line._reset_price_unit()
            if line.currency_id.compare_amounts(line.price_unit, vals['price_unit'] or 0.0):
                raise line._combo_item_price_error()
        return res

    def copy_data(self, default=None):
        """Duplicar: lo tecleado se conserva con su marca y lo demás sale del acuerdo vigente, que
        puede haber cambiado desde el pedido original. Un precio fijo vigente gana sobre lo manual.

        Sin esto se copian `discount` y `price_unit` pero no su origen (`copy=False`): el cálculo
        de la línea nueva pondría el badge del acuerdo actual junto a importes que no son suyos.
        """
        vals_list = super().copy_data(default=default)
        default = default or {}
        # `linked_line_id` no se copia y el core solo pone `linked_virtual_id` en productos de un
        # combo nuevo: los de un combo reconfigurado en la orden guardada quedarían sin paquete.
        vals_by_id = {line.id: vals for line, vals in zip(self, vals_list)}
        for line, vals in zip(self, vals_list):
            package_id = line.linked_line_id.id
            if line.combo_item_id and package_id in vals_by_id:
                package_vals = vals_by_id[package_id]
                package_vals['virtual_id'] = package_vals.get('virtual_id') or f'copy-{package_id}'
                vals['linked_virtual_id'] = package_vals['virtual_id']
        for line, vals in zip(self, vals_list):
            if not line._admits_partner_agreement():
                continue
            fixed_now = line._has_fixed_price_agreement()
            if 'discount' not in default:
                if line.pricing_rule_type == 'manual' and not fixed_now:
                    vals.update(line._manual_discount_values(line.discount))
                else:
                    vals.pop('discount', None)
            if 'price_unit' not in default:
                if line.price_origin == 'manual' and not fixed_now:
                    vals.update(line._manual_price_values())
                else:
                    vals.pop('price_unit', None)
                    vals.pop('technical_price_unit', None)
        return vals_list

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
            if (
                line.pricing_rule_type != 'fixed_price'
                and not line.is_downpayment
                and line._has_core_manual_price()
            ):
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
        # Depende de la tarifa del pedido, pero la regla en caché (`pricelist_item_id`, que no
        # depende de ella) puede ser la de la tarifa anterior: como `_recompute_prices` del core.
        self.invalidate_recordset(['pricelist_item_id'])
        # Líneas que el core no recalcula: conservan su origen tal cual.
        core_skipped = {
            line: (
                line.price_origin, line.price_origin_label,
                line.combo_price_snapshot, line.combo_package_price,
            )
            for line in self
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
                (
                    line.price_origin, line.price_origin_label,
                    line.combo_price_snapshot, line.combo_package_price,
                ) = core_skipped[line]
                continue
            if not force_recompute and (line._keeps_saved_pricing() or line._keeps_manual_price()):
                continue
            if line.product_type == 'combo':
                continue
            rule = line._get_agreement_rule()
            if rule.get('type') == 'fixed_price':
                line._apply_fixed_price_agreement(rule)
        # Cambio de moneda en el formulario: la tarifa recalcula las demás líneas en la moneda
        # nueva, pero las que conservan su precio (manual, descuento global…) se convierten aquí.
        # Por código, `_origin` ya trae la moneda nueva: lo convierte `SaleOrder.write`.
        for line in self:
            origin = line._origin
            if (
                origin.id
                and origin.currency_id
                and line.currency_id
                and origin.currency_id != line.currency_id
                and line._keeps_price_on_currency_change()
                # Ya convertido (el cálculo puede correr más de una vez en el mismo onchange).
                and not origin.currency_id.compare_amounts(line.price_unit, origin.price_unit)
            ):
                line.with_context(skip_manual_discount_breakdown=True).update({
                    'price_unit': line._convert_price_currency(origin.price_unit, origin.currency_id),
                    'technical_price_unit': line._convert_price_currency(
                        origin.technical_price_unit, origin.currency_id),
                })

    def _has_core_manual_price(self):
        """Mismo criterio que el core: `price_unit` distinto del último precio calculado."""
        self.ensure_one()
        return bool(self._get_price_currency().compare_amounts(self.technical_price_unit, self.price_unit))

    def _get_price_currency(self):
        self.ensure_one()
        return self.currency_id or self.company_id.currency_id or self.env.company.currency_id

    def _get_display_price_ignore_combo(self):
        # La tarifa siempre va dentro de `price_unit`, también las reglas de porcentaje: `discount`
        # queda para el acuerdo o el descuento manual.
        self.ensure_one()
        if self.env.context.get('combo_package_price') is not None:
            return self.env.context['combo_package_price']
        return self._get_pricelist_price()

    def _get_combo_item_display_price(self):
        # El core reparte `_get_display_price_ignore_combo()` del paquete guardado: aquí, el precio
        # repartido del paquete del pedido, que en el onchange ya trae la tarifa y la moneda nuevas.
        self.ensure_one()
        package_price = self._get_combo_package_line()._get_combo_package_price()
        return super(
            SaleOrderLine, self.with_context(combo_package_price=package_price)
        )._get_combo_item_display_price()

    def _get_combo_package_price(self):
        self.ensure_one()
        return self._get_combo_package_pricing()['price']

    def _get_combo_package_pricing(self):
        """Precio que la línea del paquete reparte entre sus productos y de dónde sale.

        Con las mismas reglas que el resto de líneas: una línea guardada conserva el precio fijo
        ya aplicado y no toma un acuerdo nuevo hasta "Actualizar precios" o un cambio de cliente.
        """
        self.ensure_one()
        pricelist_price = self._get_pricelist_price()
        no_agreement = {'price': pricelist_price, 'pricelist_price': pricelist_price, 'agreement': False}
        origin = self._origin
        if not self.env.context.get('force_price_recomputation') and self._keeps_saved_pricing():
            if origin.pricing_rule_type != 'fixed_price':
                return no_agreement
            price = origin.product_uom_id._compute_price(origin.combo_package_price, self.product_uom_id)
            if origin.currency_id != self.currency_id:
                price = self._convert_price_currency(price, origin.currency_id)
            label = origin.pricing_rule_origin or 'Precio Fijo'
        else:
            rule = self._get_agreement_rule()
            if rule.get('type') != 'fixed_price':
                return no_agreement
            price = self._fixed_price_in_line_units(rule['price'])
            label = self._agreement_discount_badge(rule)[1][1]
        return {
            'price': price,
            'pricelist_price': pricelist_price,
            'agreement': {'type': 'fixed_price', 'label': label, 'price': price},
        }

    def _get_combo_price_breakdown(self):
        """Foto del reparto del precio del paquete con la fórmula de `_get_combo_item_display_price`.

        Si el core cambia esa fórmula, `unit_price` deja de coincidir con `price_unit`.
        """
        self.ensure_one()
        if not self.combo_item_id:
            return False
        combo_line = self._get_combo_package_line()
        if not combo_line:
            return False
        currency = self.currency_id
        pricing = combo_line._get_combo_package_pricing()
        package_price = pricing['price']

        def to_order_currency(combo, amount):
            return combo.currency_id._convert(
                from_amount=amount,
                to_currency=currency,
                company=self.company_id,
                date=self.order_id.date_order,
            )

        groups = []
        for combo in combo_line.product_template_id.sudo().combo_ids:
            reference_item = min(
                combo.combo_item_ids,
                key=lambda item: item.currency_id._convert(
                    from_amount=item.lst_price,
                    to_currency=combo.currency_id,
                    company=combo.company_id or self.env.company,
                    date=self.env.cr.now(),
                ),
                default=combo.combo_item_ids,
            )
            groups.append({
                'combo_id': combo.id,
                'name': combo.name,
                'reference': to_order_currency(combo, combo.base_price),
                'reference_product': reference_item.product_id.display_name or '',
            })
        total_reference = sum(group['reference'] for group in groups)
        # Divisor 1 con referencias en 0: el reparto sale mal y el ajuste lo corrige, como el core.
        shares = {
            group['combo_id']: group['reference'] * package_price / (total_reference or 1)
            for group in groups
        }
        rounded = {combo_id: currency.round(share) for combo_id, share in shares.items()}
        delta = package_price - sum(rounded.values())
        own_combo = self.combo_item_id.combo_id
        adjustment = delta if delta and groups and groups[-1]['combo_id'] == own_combo.id else 0.0
        extra_price = self.combo_item_id.extra_price
        attributes_extra = self.product_id._get_no_variant_attributes_price_extra(
            self.product_no_variant_attribute_value_ids
        )
        share_rounded = rounded.get(own_combo.id, 0.0)
        return {
            'pricelist_price': pricing['pricelist_price'],
            'agreement': pricing['agreement'],
            'package_price': package_price,
            'package_price_origin': (
                f"Tarifa: {combo_line.order_id.pricelist_id.name}"
                if combo_line.pricelist_item_id else 'Precio de venta'
            ),
            'currency_id': currency.id,
            'total_reference': total_reference,
            'groups': groups,
            'combo_id': own_combo.id,
            'share_raw': shares.get(own_combo.id, 0.0),
            'share_rounded': share_rounded,
            'rounding_adjustment': currency.round(adjustment),
            'extra_price': extra_price,
            'attributes_extra': attributes_extra,
            'unit_price': share_rounded + adjustment + extra_price + attributes_extra,
        }

    def _reset_price_unit(self):
        force_recompute = self.env.context.get('force_price_recomputation')
        for line in self.with_context(sale_write_from_compute=True):
            if line.reward_id:
                # Lo decide `sale_loyalty`: `_reset_loyalty` deja el precio en 0 con el técnico
                # anterior, y eso no es un precio manual.
                super(SaleOrderLine, line)._reset_price_unit()
                line._set_price_origin()
                continue
            if line.product_type == 'combo':
                # El paquete vale 0, como en el core: su precio, de tarifa o fijo, lo reparten
                # sus productos.
                super(SaleOrderLine, line)._reset_price_unit()
                line.combo_package_price = line._get_combo_package_price()
                line.combo_price_snapshot = False
                line._set_price_origin()
                continue
            if line._keeps_manual_price() and not (force_recompute and line._has_fixed_price_agreement()):
                # Precio manual: no se toca; el precio fijo de acuerdo sí gana sobre él.
                continue
            if not force_recompute and line._keeps_saved_pricing():
                # Línea guardada: la tarifa se recalcula (cantidad/UdM), pero un precio fijo
                # de acuerdo ya aplicado se conserva congelado, convertido a la UdM y a la moneda
                # de la línea (cambio de tarifa). Se parte de `technical_price_unit`, sin redondear:
                # desde `price_unit`, ya redondeado en la otra moneda, 300 MXN → 16.33 USD → 299.96.
                frozen_type = line._origin.pricing_rule_type
                frozen_price = line._origin.product_uom_id._compute_price(
                    line._origin.technical_price_unit, line.product_uom_id)
                if line._origin.currency_id != line.currency_id:
                    frozen_price = line._convert_price_currency(frozen_price, line._origin.currency_id)
                super(SaleOrderLine, line)._reset_price_unit()
                line.combo_price_snapshot = line._get_combo_price_breakdown()
                if frozen_type == 'fixed_price' and not line.combo_item_id:
                    line.price_unit = frozen_price
                    line.technical_price_unit = frozen_price
                    line.price_origin = 'fixed_price'
                    line.price_origin_label = line._origin.pricing_rule_origin or 'Precio Fijo'
                else:
                    line._set_price_origin()
                continue
            super(SaleOrderLine, line)._reset_price_unit()
            line.combo_price_snapshot = line._get_combo_price_breakdown()
            rule = line._get_agreement_rule()
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
        # Las líneas de recompensa conservan su descuento, como en el core: lo decide `sale_loyalty`.
        rewards = self.filtered('reward_id')
        for line in rewards:
            # Sin acuerdo; el regalo conserva la insignia de su promoción.
            line.technical_discount = line.discount
            line.pricing_rule_type = line.pricing_rule_type or 'none'
            line.pricing_rule_origin = line.pricing_rule_origin or ''
            line.pricing_rule_id = False
        self -= rewards
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
        manual = {
            line: (line.discount, line.pricing_rule_origin)
            for line in self
            if line.pricing_rule_type == 'manual' and line._keeps_manual_values()
        }
        # Promoción por línea: la misma regla que el descuento manual. Un precio fijo vigente solo
        # gana en un recálculo forzado: en una línea guardada se aplica con «Actualizar precios».
        promo = {
            line: (line.discount, line.pricing_rule_origin, line.promo_reward_id)
            for line in self
            if line.promo_reward_id
            and line._keeps_manual_values()
            and (line in frozen or not line._has_fixed_price_agreement())
        }
        # El % que deja el core es el de la tarifa, que ya va en el precio: se descarta siempre.
        # Las líneas de precio fijo no pasan por el core: llamado directo sobre líneas guardadas
        # (p. ej. `_recompute_prices`), su % sería un write real y la restricción lo rechazaría.
        fixed_lines = self.filtered(lambda line: line.pricing_rule_type == 'fixed_price')
        super(SaleOrderLine, (self - fixed_lines).with_context(skip_manual_discount_breakdown=True))._compute_discount()
        # Llamado directo sobre líneas guardadas (p. ej. `_recompute_prices`), asignar `discount`
        # pasa por write(): no es un descuento manual.
        for line in self.with_context(skip_manual_discount_breakdown=True):
            if line.promo_reward_id and line not in promo:
                line.promo_reward_id = False
            if line in promo:
                discount, origin, reward = promo[line]
                badge = ('promo', origin, False)
                line.promo_reward_id = reward
            elif line in manual and (line in frozen or not line._has_fixed_price_agreement()):
                discount, origin = manual[line]
                badge = ('manual', origin, False)
            elif line in frozen:
                discount, rule_type, origin, rule_id = frozen[line]
                if rule_type not in AGREEMENT_DISCOUNT_TYPES:
                    discount = 0.0
                badge = (rule_type, origin, rule_id)
            else:
                discount, badge = line._agreement_discount_badge(line._get_agreement_rule())
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
        # Después del bucle: el paquete, si está en `self`, ya tiene su acuerdo calculado.
        self.filtered('combo_item_id')._sync_combo_package_agreement()

    def _get_combo_package_agreement(self):
        """Descuento y badge que un producto de combo copia de su paquete."""
        self.ensure_one()
        package = self._get_combo_package_line()
        origin = package.pricing_rule_origin
        return {
            'discount': package.discount,
            'technical_discount': package.discount,
            'pricing_rule_type': package.pricing_rule_type or 'none',
            'pricing_rule_origin': f"Del paquete: {origin}" if origin else '',
            'pricing_rule_id': package.pricing_rule_id.id,
        }

    def _get_combo_package_line(self):
        """Paquete de esta línea tal como está en el pedido.

        En el onchange del pedido, `linked_line_id` apunta al paquete guardado y no a la copia que
        se está editando: su descuento sería el anterior. Un paquete sin guardar ya es esa copia, y
        compararlo por `_origin` (vacío en todas las líneas nuevas) daría la primera del pedido.
        """
        self.ensure_one()
        package = self._get_linked_line()
        if not package._origin.id:
            return package
        return self.order_id.order_line.filtered(
            lambda line: line._origin == package._origin
        )[:1] or package

    def _check_combo_pricing_vals(self, vals):
        """Precio y descuento de un combo los fija su paquete: por importación o RPC solo se acepta
        el valor que ya tienen, como en el formulario, donde son de solo lectura.

        Un `price_unit` que llega con `product_id` se revisa tras el write, contra el reparto.
        """
        if self.env.context.get('sale_write_from_compute') or self.env.context.get('skip_manual_discount_breakdown'):
            return
        check_price = 'price_unit' in vals and 'product_id' not in vals
        digits = self.env['decimal.precision'].precision_get('Discount')
        for line in self:
            price_changed = check_price and line._get_price_currency().compare_amounts(line.price_unit, vals['price_unit'] or 0.0)
            if price_changed and line.product_type == 'combo':
                raise ValidationError(_(
                    "El paquete «%(package)s» no lleva precio propio: su precio lo reparten sus productos.",
                    package=line.product_id.display_name,
                ))
            if not line.combo_item_id:
                continue
            if price_changed:
                raise line._combo_item_price_error()
            if 'discount' in vals and float_compare(line.discount, vals['discount'] or 0.0, precision_digits=digits):
                raise ValidationError(_(
                    "No se puede cambiar el descuento de «%(product)s»: es parte del combo «%(package)s». "
                    "El descuento se pone en el paquete.",
                    product=line.product_id.display_name,
                    package=line._get_linked_line().product_id.display_name,
                ))

    def _combo_item_price_error(self):
        self.ensure_one()
        return ValidationError(_(
            "No se puede cambiar el precio de «%(product)s»: es parte del combo «%(package)s» "
            "y su precio sale del reparto del paquete. Cambia el precio o el descuento en el paquete.",
            product=self.product_id.display_name,
            package=self._get_linked_line().product_id.display_name,
        ))

    def _sync_combo_package_agreement(self):
        for line in self.with_context(skip_manual_discount_breakdown=True):
            # Al reconfigurar un combo sin guardar, el onchange ya sacó del pedido sus productos
            # anteriores: sin `order_id`, `_get_linked_line` no encuentra el paquete y lanza.
            if not line.order_id or not line._get_linked_line():
                continue
            values = line._get_combo_package_agreement()
            # En una línea guardada cada asignación es un write: hacia precio fijo, `discount`
            # (= 0) antes que el badge; en otro caso, el badge antes, por la restricción.
            discount_first = values['pricing_rule_type'] == 'fixed_price'
            keys = sorted(values, key=lambda key: ('discount' in key) != discount_first)
            for key in keys:
                current = line[key].id if key == 'pricing_rule_id' else line[key]
                if current != values[key]:
                    line[key] = values[key]

    def _apply_fixed_price_agreement(self, rule):
        """Precio fijo de acuerdo: gana sobre la tarifa y sobre cualquier descuento o precio manual.

        El precio de la regla está en la UdM del producto y en la moneda de la compañía: se
        convierte a la UdM y a la moneda de la línea.
        `discount` va primero: en una línea guardada cada asignación es un write y la restricción
        de precio fijo vería el descuento anterior.
        """
        self.ensure_one()
        line = self.with_context(skip_manual_discount_breakdown=True)
        price = self._fixed_price_in_line_units(rule['price'])
        _discount, (rule_type, origin, rule_id) = self._agreement_discount_badge(rule)
        line.discount = 0.0
        line.technical_discount = 0.0
        line.price_unit = price
        line.technical_price_unit = price
        line.pricing_rule_type = rule_type
        line.pricing_rule_origin = origin
        line.pricing_rule_id = rule_id
        line.price_origin = 'fixed_price'
        line.price_origin_label = origin

    def _fixed_price_in_line_units(self, base_price):
        """Precio de acuerdo, en la UdM del producto y la moneda de la compañía, llevado a la UdM y
        la moneda de la línea."""
        self.ensure_one()
        price = self.product_id.uom_id._compute_price(base_price, self.product_uom_id)
        return self._convert_price_currency(price, self._get_fixed_price_company().currency_id)

    def _get_fixed_price_company(self):
        self.ensure_one()
        return self.order_id.company_id or self.company_id or self.env.company

    def _convert_price_currency(self, price, from_currency):
        """Precio de `from_currency` a la moneda de la línea, como la tarifa del core: tasa de la
        fecha del pedido y sin redondear (lo redondean los dígitos del campo)."""
        self.ensure_one()
        if not from_currency or not self.currency_id or from_currency == self.currency_id:
            return price
        return from_currency._convert(
            price,
            self.currency_id,
            self._get_fixed_price_company(),
            self._get_order_date() or fields.Date.context_today(self),
            round=False,
        )

    def _keeps_price_on_currency_change(self):
        """Línea cuyo precio no recalcula la tarifa al cambiar la moneda del pedido: precio manual,
        descuento global, envío o recompensa. Se convierte tal cual a la moneda nueva.

        El precio fijo se convierte aparte: congelado en `_reset_price_unit` o reaplicado.
        """
        self.ensure_one()
        return bool(
            self.order_id
            and not self.display_type
            and not self.is_downpayment
            and not self.qty_invoiced
            and self.pricing_rule_type != 'fixed_price'
            and (self._is_global_discount() or self._has_core_manual_price() or self._keeps_manual_price())
        )

    def _admits_partner_agreement(self, product=None):
        """Línea que recibe el acuerdo comercial del cliente, sea bien o servicio. `product`
        evalúa el producto que llega en `write` antes de escribirlo.

        Un producto de combo no: el acuerdo es el del paquete.
        """
        self.ensure_one()
        return bool(
            (product or self.product_id)
            and not self.display_type
            and not self.combo_item_id
            and self.order_id.partner_id
            and not self.is_reward_line
            and not self.is_downpayment
            and not self._is_global_discount()
        )

    def _get_agreement_rule(self, product=None):
        """Acuerdo del cliente para `product` (por omisión, el de la línea); `{}` si la línea no lo
        admite."""
        self.ensure_one()
        product = product or self.product_id
        if not self._admits_partner_agreement(product):
            return {}
        return self.order_id.partner_id._get_partner_pricing_rule(product)

    def _agreement_discount_badge(self, rule):
        """Descuento y badge (tipo, origen, regla) que da el acuerdo `rule`."""
        rule_id = rule.get('rule') and rule['rule'].id or False
        if rule.get('type') == 'fixed_price':
            return 0.0, ('fixed_price', rule.get('origin_label') or 'Precio Fijo', rule_id)
        if rule.get('type') == 'discount':
            return rule['discount'], (rule.get('origin_type') or 'product', rule.get('origin_label') or '', rule_id)
        return 0.0, ('none', '', False)

    def _is_listed_for_line_promotion(self):
        """Línea que el selector de promociones muestra, elegible o no."""
        self.ensure_one()
        return bool(
            self.product_id
            and not self.display_type
            and not self.combo_item_id
            and not self.is_reward_line
            and not self.is_downpayment
            and self.product_id != self.company_id.sale_discount_product_id
        )

    def _get_line_promotion_block_reason(self):
        """Motivo por el que una línea listada no puede tomar una promoción."""
        self.ensure_one()
        if self.pricing_rule_type == 'fixed_price':
            return _("Precio fijo de acuerdo")
        if self.qty_invoiced > 0:
            return _("Ya facturada")
        return False

    def _admits_line_promotion(self):
        self.ensure_one()
        return self._is_listed_for_line_promotion() and not self._get_line_promotion_block_reason()

    def _get_lines_with_price_batch(self):
        """`_get_lines_with_price` del core, que solo admite una línea, para varias."""
        lines = self.browse()
        for line in self:
            lines |= line._get_lines_with_price()
        return lines

    @api.depends(
        'order_id.state', 'order_id.partner_id', 'order_id.pricelist_id', 'order_id.code_enabled_rule_ids',
        'order_id.order_line.product_id', 'order_id.order_line.product_uom_qty',
        'order_id.order_line.price_unit', 'order_id.order_line.discount',
        'order_id.order_line.price_subtotal', 'order_id.order_line.price_total',
        'order_id.order_line.tax_ids', 'order_id.order_line.pricing_rule_type',
        'order_id.order_line.promo_reward_id', 'order_id.order_promo_reward_id',
    )
    def _compute_promo_available(self):
        def option(reward, difference, applied):
            return {
                'reward_id': reward.id,
                'name': reward.program_id.name,
                'discount': reward.discount,
                'difference': difference,
                'applied': applied,
                'scope': reward.discount_applicability,
            }

        self.promo_available_info = False
        self.promo_available_count = 0
        for order, lines in self.grouped('order_id').items():
            if order.state not in ('draft', 'sent'):
                continue
            # Es solo informativo: quien lee el pedido puede no tener acceso a lealtad.
            available = order.sudo()._get_line_promotion_rewards()
            for line in lines:
                if line.combo_item_id:
                    # Un producto del combo solo informa la promoción de su paquete: se elige allá.
                    package_reward = line._get_combo_package_line().promo_reward_id.sudo()
                    if package_reward:
                        line.promo_available_info = {
                            'options': [option(package_reward, 0.0, True)],
                            'fallback': False,
                            'from_package': True,
                        }
                    continue
                applied = line.promo_reward_id.sudo()
                rewards = available.get(line, self.env['loyalty.reward']) - applied
                if not rewards and not applied:
                    continue
                # El paquete de un combo vale 0: su importe es el de sus productos.
                price_lines = line._get_lines_with_price()
                base_amount = sum(price_line._get_amount_without_discount() for price_line in price_lines)

                def difference(discount):
                    return line.currency_id.round(base_amount * (discount - line.discount) / 100)

                line.promo_available_count = len(rewards)
                options = [
                    option(reward, difference(reward.discount), reward == applied)
                    for reward in (rewards | applied).sorted(key=lambda reward: (-reward.discount, reward.id))
                ]
                fallback = line._promotion_fallback_hint(price_lines, difference) if applied else False
                if fallback:
                    # Quitar la promoción de pedido de una línea la quita de todo el pedido.
                    order_reward = line.order_id.order_promo_reward_id.sudo()
                    fallback['scope'] = 'order' if order_reward and applied == order_reward else 'specific'
                line.promo_available_info = {'options': options, 'fallback': fallback}

    def _promotion_fallback_hint(self, price_lines, difference):
        """Vuelta al acuerdo vigente que el selector ofrece como «Sin promoción»."""
        self.ensure_one()
        rule = self._get_agreement_rule()
        if rule.get('type') == 'fixed_price':
            fixed_subtotal = self.currency_id.round(rule['price'] * self.product_uom_qty)
            return {
                'label': rule.get('origin_label') or _("Precio Fijo"),
                'difference': self.currency_id.round(sum(price_lines.mapped('price_subtotal')) - fixed_subtotal),
            }
        agreement = rule['discount'] if rule.get('type') == 'discount' else 0.0
        return {
            'label': rule.get('origin_label') or _("sin acuerdo"),
            'difference': difference(agreement),
        }

    def action_set_line_promotion(self, reward_id=False):
        """Aplica la promoción `reward_id` a la línea, o la quita si es falso. Lo usa el selector
        de la estrella."""
        self.ensure_one()
        if self.order_id.state not in ('draft', 'sent'):
            raise UserError(_("Las promociones por línea solo se eligen en una cotización."))
        reward = self.env['loyalty.reward'].browse(reward_id).exists()
        order = self.order_id
        if reward and reward.discount_applicability == 'order':
            order._set_order_promotion(reward)
        elif not reward and order.order_promo_reward_id and self.promo_reward_id == order.order_promo_reward_id:
            # La promoción de pedido es de todo o nada: quitarla de una línea la quita del pedido.
            order._remove_order_promotion()
        elif reward:
            if order.order_promo_reward_id:
                raise self._order_promotion_lock_error()
            if reward != self.promo_reward_id:
                self._apply_line_promotion(reward)
        else:
            self._drop_line_promotion()
        self.order_id._reevaluate_line_promotions()
        return True

    def _get_taxes_without_discount(self):
        self.ensure_one()
        return self.tax_ids.compute_all(
            self.price_unit, self.currency_id, self.product_uom_qty,
            product=self.product_id, partner=self.order_id.partner_shipping_id,
        )

    def _get_amount_without_discount(self):
        """Importe sin IVA de la línea antes de cualquier descuento."""
        return self._get_taxes_without_discount()['total_excluded']

    def _get_line_promotion_threshold_amounts(self):
        """Importe sin IVA y con IVA con el que la línea cuenta para el mínimo de un programa.

        El de la línea en promoción se mide sin ese descuento, igual que el core no cuenta su
        propia línea de recompensa: si no, aplicar la promoción podría cancelarla. Un producto de
        combo lleva la promoción de su paquete y se mide igual.
        """
        self.ensure_one()
        promo_line = self._get_combo_package_line() if self.combo_item_id else self
        if not promo_line.promo_reward_id:
            return self.price_subtotal, self.price_total
        taxes = self._get_taxes_without_discount()
        return taxes['total_excluded'], taxes['total_included']

    def _promotion_writer(self):
        """Líneas con las que las promociones escriben su descuento: no es un descuento manual."""
        return self.with_context(skip_manual_discount_breakdown=True)

    def _line_promotion_values(self, reward):
        return {
            'discount': reward.discount,
            'technical_discount': reward.discount,
            'pricing_rule_type': 'promo',
            'pricing_rule_origin': f"Promo: {reward.program_id.name} ({reward.discount:.1f}%)",
            'pricing_rule_id': False,
            'promo_reward_id': reward.id,
        }

    def _apply_line_promotion(self, reward):
        """Sustituye el descuento de las líneas por el de `reward`, si se les ofrece."""
        for order, lines in self.grouped('order_id').items():
            available = order._get_line_promotion_rewards()
            for line in lines:
                if reward not in available[line]:
                    raise ValidationError(_(
                        "La promoción «%(reward)s» no está disponible para la línea «%(line)s».",
                        reward=reward.display_name,
                        line=line.name,
                    ))
        self._promotion_writer().write(self._line_promotion_values(reward))

    def _drop_line_promotion(self):
        """Quita la promoción de las líneas y las devuelve a su acuerdo vigente."""
        lines = self.filtered('promo_reward_id')
        if not lines:
            return
        # El paquete vuelve a su acuerdo con el cálculo del combo: su precio vale 0 y un precio
        # fijo se reparte entre sus productos, que copian el descuento.
        packages = lines.filtered(lambda line: line.product_type == 'combo')
        if packages:
            packages.with_context(skip_manual_discount_breakdown=True).write({'promo_reward_id': False})
            combo_lines = (packages | packages.linked_line_ids.filtered('combo_item_id')).with_context(
                force_price_recomputation=True, reset_manual_pricing=True,
            )
            combo_lines._compute_price_unit()
            combo_lines._compute_discount()
        for line in lines - packages:
            line._promotion_writer().write(line._promotion_fallback_values())

    def _promotion_fallback_values(self):
        """Valores con los que la línea vuelve a su acuerdo vigente al quitar su promoción."""
        self.ensure_one()
        rule = self._get_agreement_rule()
        discount, (rule_type, origin, rule_id) = self._agreement_discount_badge(rule)
        values = {
            'promo_reward_id': False,
            'discount': discount,
            'technical_discount': discount,
            'pricing_rule_type': rule_type,
            'pricing_rule_origin': origin,
            'pricing_rule_id': rule_id,
        }
        if rule_type == 'fixed_price':
            values.update(price_unit=rule['price'], technical_price_unit=rule['price'])
        return values

    @api.constrains('promo_reward_id')
    def _check_line_promotion(self):
        for line in self.filtered('promo_reward_id'):
            if not line.promo_reward_id._is_line_promotion():
                raise ValidationError(_(
                    "La recompensa «%(reward)s» no se elige por línea.",
                    reward=line.promo_reward_id.display_name,
                ))
            if not line._admits_line_promotion():
                raise ValidationError(_(
                    "La línea «%(line)s» no admite promociones.",
                    line=line.name,
                ))

    def _has_fixed_price_agreement(self):
        self.ensure_one()
        return self._get_agreement_rule().get('type') == 'fixed_price'
