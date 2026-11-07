from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools import float_compare, float_round
from collections import defaultdict
from markupsafe import Markup
import logging

from .loyalty_line_promotion import LINE_PROMOTION_PROGRAM_TYPES

_logger = logging.getLogger(__name__)
CREATE_QUOTATION_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_create_quotation'
CONFIRM_QUOTATION_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_confirm_quotation'


class SaleOrder(models.Model):
    _inherit = "sale.order"

    quotation_ref = fields.Char(
        string="Referencia",
        tracking=True,
        copy=True,
        index=True,
        help="Referencia interna del concepto, proyecto u obra cotizada",
    )
    promo_available_line_count = fields.Integer(
        string='Líneas con promoción disponible',
        compute='_compute_promo_available_line_count',
    )
    # Solo para la vista: el bloqueo de Ajustes no se aplica en el servidor.
    price_unit_edit_locked = fields.Boolean(compute='_compute_edit_locked')
    discount_edit_locked = fields.Boolean(compute='_compute_edit_locked')
    amount_discount = fields.Monetary(
        string='Descuento total',
        compute='_compute_amount_discount',
        currency_field='currency_id',
    )
    amount_before_discount = fields.Monetary(
        string='Importe antes de descuento',
        compute='_compute_amount_before_discount',
        currency_field='currency_id',
    )

    @api.depends('order_line.discount_amount', 'order_line.product_type', 'order_line.reward_id', 'order_line.price_subtotal', 'order_line.price_unit')
    def _compute_amount_discount(self):
        for order in self:
            # El paquete de combo solo repite lo que ya suman sus productos.
            lines = order.order_line.filtered(lambda line: line.product_type != 'combo')
            standard_lines = lines.filtered(lambda line: not getattr(line, 'is_reward_line', False) and not line.reward_id)
            fixed_reward_lines = lines.filtered(
                lambda line: (getattr(line, 'is_reward_line', False) or bool(line.reward_id))
                and (line.price_subtotal < 0 or line.price_unit < 0)
            )
            order.amount_discount = (
                sum(standard_lines.mapped('discount_amount'))
                + sum(-line.price_subtotal if line.price_subtotal < 0 else -line.price_unit * line.product_uom_qty for line in fixed_reward_lines)
            )

    @api.depends('amount_untaxed', 'amount_discount', 'currency_id')
    def _compute_amount_before_discount(self):
        # No se usa `amount_undiscounted`: no redondea por línea y podría no cuadrar con el descuento mostrado.
        for order in self:
            amount = order.amount_untaxed + order.amount_discount
            order.amount_before_discount = order.currency_id.round(amount) if order.currency_id else amount

    @api.depends('company_id')
    @api.depends_context('uid')
    def _compute_edit_locked(self):
        can_edit_price = self.env.user.has_group('entintados_pdv.group_sale_edit_price_unit')
        can_edit_discount = self.env.user.has_group('entintados_pdv.group_sale_edit_discount')
        for order in self:
            order.price_unit_edit_locked = not (order.company_id.sale_price_unit_editable or can_edit_price)
            order.discount_edit_locked = not (order.company_id.sale_discount_editable or can_edit_discount)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            opportunity_id = vals.get('opportunity_id', self.env.context.get('default_opportunity_id'))
            if opportunity_id:
                self.env['crm.lead'].browse(opportunity_id)._check_can_create_quotation()
        orders = super().create(vals_list)
        for order in orders:
            order._sync_opportunity_stage()
        return orders

    def write(self, vals):
        if vals.get('opportunity_id'):
            self.env['crm.lead'].browse(vals['opportunity_id'])._check_can_create_quotation()
        if 'partner_id' in vals:
            self = self.with_context(force_price_recomputation=True)
        # Cambio de tarifa o de cliente (y con ellos de moneda) por código, RPC, importación o el
        # formulario: tras el write `_origin` ya trae la moneda nueva, así que las líneas que la
        # tarifa no recalcula se convierten aquí desde la anterior. El precio fijo congelado
        # también, salvo con recálculo forzado (cambio de cliente), que reaplica la regla vigente.
        force = self.env.context.get('force_price_recomputation')
        previous_prices = {}
        previous_packages = {}
        if 'pricelist_id' in vals or 'partner_id' in vals:
            frozen = self.order_line.filtered(
                lambda line: not force and line.pricing_rule_type == 'fixed_price' and not line.qty_invoiced
            )
            # Un combo de precio fijo congela su precio en el paquete (`combo_package_price`) y sus
            # productos lo reparten: se convierte el paquete y se reparte otra vez, no producto a producto.
            packages = frozen.filtered(lambda line: line.product_type == 'combo')
            previous_prices = {
                line: (line.currency_id, line.product_uom_id, line.price_unit, line.technical_price_unit)
                for line in self.order_line
                if line._keeps_price_on_currency_change()
                or (line in frozen and line not in packages and not line.combo_item_id)
            }
            previous_packages = {
                line: (line.currency_id, line.combo_package_price)
                for line in packages
            }
        res = super().write(vals)
        for order in self:
            if 'opportunity_id' in vals:
                order._sync_opportunity_stage()
            if 'partner_id' in vals and order.state in ('draft', 'sent'):
                # El core solo reasigna pricelist_id automáticamente en draft:
                # si no se pasa pricelist_id explícito al cambiar cliente en 'sent', se asigna la tarifa del cliente.
                if 'pricelist_id' not in vals:
                    pricelist = order.with_company(order.company_id).partner_id.property_product_pricelist
                    if pricelist and pricelist != order.pricelist_id:
                        order.pricelist_id = pricelist
                order._recompute_pricing_rules()
            if vals.get('state') == 'sale' and order.state == 'sale':
                order._cancel_sibling_quotations()
                order._advance_opportunity_to_closed()
                if order.opportunity_id:
                    order._schedule_confirm_quotation_activity()
        for line, (old_currency, old_uom, old_price, old_technical) in previous_prices.items():
            if not line.exists() or line.currency_id == old_currency:
                continue
            fixed_price = line.pricing_rule_type == 'fixed_price'
            if fixed_price and old_uom and old_uom != line.product_uom_id:
                # Un cambio de UdM en el mismo guardado ya convirtió el precio fijo a la UdM nueva
                # dentro del super(), pero no de moneda: se compara contra el capturado en esa UdM.
                old_price = old_uom._compute_price(old_price, line.product_uom_id)
                old_technical = old_uom._compute_price(old_technical, line.product_uom_id)
            # Precio ya recalculado o convertido (p. ej. enviado por el formulario).
            if old_currency.compare_amounts(line.price_unit, old_price):
                continue
            technical = line._convert_price_currency(old_technical, old_currency)
            line.with_context(skip_manual_discount_breakdown=True).write({
                # Precio fijo: ambos campos salen del valor sin redondear. Si difieren, el siguiente
                # cambio de tarifa en el formulario toma la línea por precio manual y no la convierte.
                'price_unit': (
                    technical if fixed_price
                    else line._convert_price_currency(old_price, old_currency)
                ),
                'technical_price_unit': technical,
            })
        for package, (old_currency, old_price) in previous_packages.items():
            if not package.exists() or package.currency_id == old_currency:
                continue
            if old_currency.compare_amounts(package.combo_package_price, old_price):
                continue
            package.combo_package_price = package._convert_price_currency(old_price, old_currency)
            package.order_id.order_line.filtered(
                lambda line: line.combo_item_id and line._get_linked_line() == package
            )._reset_price_unit()
        if vals.keys() & {'order_line', 'partner_id', 'pricelist_id'}:
            if not self.env.context.get('in_loyalty_update') and not self.env.context.get('gift_reevaluation'):
                self._reevaluate_line_promotions()
            # El core solo reevalúa los regalos al confirmar o con «Promociones».
            if not self.env.context.get('gift_reevaluation') and not self.env.context.get('in_loyalty_update'):
                for order in self.filtered(
                    lambda order: order.state in ('draft', 'sent') and order._gift_reward_lines()
                ):
                    order.with_context(gift_reevaluation=True, in_loyalty_update=True)._update_programs_and_rewards()
        return res

    @api.onchange('partner_id')
    def _onchange_partner_id_entintados_rules(self):
        for order in self:
            if not order.partner_id or order.state not in ('draft', 'sent'):
                continue
            if order.partner_id.property_product_pricelist:
                order.pricelist_id = order.partner_id.property_product_pricelist
            order._recompute_pricing_rules()
            order.show_update_pricelist = False

    @api.onchange('order_line')
    def _onchange_order_line(self):
        # El core copia el `discount` del paquete a sus productos; el badge del acuerdo también.
        res = super()._onchange_order_line()
        self.order_line.filtered('combo_item_id')._sync_combo_package_agreement()
        return res

    def _get_update_prices_lines(self):
        # Excluir líneas con formulación/receta entintada: su precio depende de los componentes y base,
        # y sus envases/descuento están protegidos contra edición directa.
        lines = super()._get_update_prices_lines()
        return lines.filtered(lambda line: not (hasattr(line, '_tint_recipe') and line._tint_recipe()))

    def _recompute_prices(self):
        # "Actualizar precios": el core pone discount = 0 y llama a _compute_discount() sin forzar;
        # forzado, se reaplica el acuerdo vigente en vez de releer ese 0 como acuerdo congelado.
        # Ese discount = 0 tampoco es un descuento manual, y el manual previo se restaura después.
        # Los productos de un combo no: copian el descuento de su paquete al restaurarlo.
        manual_lines = self.order_line.filtered(
            lambda line: line.pricing_rule_type == 'manual' and not line.combo_item_id
        )
        manual_discounts = {line: line.discount for line in manual_lines}
        promo_discounts = {line: line.discount for line in self.order_line.filtered('promo_reward_id')}
        res = super(SaleOrder, self.with_context(
            force_price_recomputation=True, skip_manual_discount_breakdown=True,
        ))._recompute_prices()
        for line, discount in manual_discounts.items():
            if line.pricing_rule_type == 'manual' and line.discount != discount:
                line.with_context(skip_manual_discount_breakdown=True).write({
                    'discount': discount,
                    **line._manual_discount_values(discount),
                })
        # Igual que el manual: el core ya puso `discount = 0` antes de recalcular.
        for line, discount in promo_discounts.items():
            if line.promo_reward_id and line.discount != discount:
                line.with_context(skip_manual_discount_breakdown=True).write({
                    'discount': discount,
                    'technical_discount': discount,
                })
        self._reevaluate_line_promotions()
        return res

    def _recompute_pricing_rules(self):
        for order in self:
            lines_to_update = order.order_line.filtered(
                lambda l: l.product_id
                and not l.display_type
                and not getattr(l, 'is_reward_line', False)
            )
            if lines_to_update:
                # Como `_recompute_prices` del core: la regla de tarifa en caché puede ser la de la
                # tarifa anterior (`pricelist_item_id` no depende de la tarifa del pedido).
                lines_to_update.invalidate_recordset(['pricelist_item_id'])
                lines_to_update.with_context(force_price_recomputation=True)._reset_price_unit()
                lines_to_update.with_context(force_price_recomputation=True)._compute_price_unit()
                lines_to_update.with_context(force_price_recomputation=True)._compute_discount()

    def _sync_opportunity_stage(self):
        """Al crear/vincular una cotización, mueve la oportunidad a la etapa de Cotización."""
        for order in self:
            if not order.opportunity_id:
                continue
            quotation_stage = self.env['crm.stage'].search([('stage_type', '=', 'quotation')], limit=1)
            if not quotation_stage:
                continue
            lead = order.opportunity_id
            
            if not lead.expected_revenue and order.amount_total:
                lead.with_context(skip_stage_sequence_check=True).write({
                    'expected_revenue': order.amount_total
                })
                
            order._schedule_create_quotation_activity()
            
            if lead.stage_id.id == quotation_stage.id:
                continue
            ordered_stages = self.env['crm.stage'].search([], order='sequence, id')
            stage_ids = ordered_stages.ids
            if lead.stage_id.id not in stage_ids:
                continue
            current_index = stage_ids.index(lead.stage_id.id)
            quotation_index = stage_ids.index(quotation_stage.id)
            # Solo avanza si la oportunidad todavía no ha llegado (o pasado) a Cotización
            if current_index < quotation_index:
                lead.with_context(skip_stage_sequence_check=True).write({
                    'stage_id': quotation_stage.id
                })

    def _cancel_sibling_quotations(self):
        """Al confirmar una cotización, cancela las demás de la misma oportunidad."""
        for order in self:
            if not order.opportunity_id:
                continue
            siblings = self.env['sale.order'].search([
                ('opportunity_id', '=', order.opportunity_id.id),
                ('id', '!=', order.id),
                ('state', 'not in', ['sale', 'cancel']),
            ])
            if siblings:
                siblings.action_cancel()
            
    def _advance_opportunity_to_closed(self):
        """Al confirmar la venta, mueve la oportunidad a la etapa de Venta Cerrada."""
        for order in self:
            if not order.opportunity_id:
                continue
            closed_stage = self.env['crm.stage'].search([('stage_type', '=', 'closed')], limit=1)
            if not closed_stage:
                continue
            lead = order.opportunity_id
            if lead.stage_id.id == closed_stage.id:
                continue
            lead.with_context(skip_stage_sequence_check=True).write({
                'stage_id': closed_stage.id
            })

    def _schedule_create_quotation_activity(self):
        """Registra la creación de la cotización como actividad ya realizada (permite duplicados)."""
        self._log_quotation_activity(CREATE_QUOTATION_ACTIVITY_XMLID, _('Se creó la cotización %s.') % self.name)

    def _schedule_confirm_quotation_activity(self):
        """Registra la confirmación de venta como actividad ya realizada."""
        self._log_quotation_activity(CONFIRM_QUOTATION_ACTIVITY_XMLID, _('Se confirmó la orden de venta %s.') % self.name)

    def _log_quotation_activity(self, activity_xmlid, note):
        """Agenda la actividad en la cotización a nombre del vendedor y la marca como hecha."""
        self.ensure_one()
        activity_type = self.env.ref(activity_xmlid, raise_if_not_found=False)
        if not activity_type:
            return
        activity = self.activity_schedule(
            activity_type_id=activity_type.id,
            user_id=self.opportunity_id.user_id.id or self.env.uid,
            note=note,
        )
        if activity:
            activity.action_feedback(feedback=note)
    def _get_no_effect_on_threshold_lines(self):
        lines = super()._get_no_effect_on_threshold_lines()
        if self.env.context.get('exclude_protected_discount_lines'):
            protected = self.order_line.filtered(
                lambda l: l.pricing_rule_type == 'fixed_price'
                or getattr(l, 'is_tint_colorant', False)
                or getattr(l.product_id, 'tint_role', False) == 'colorant'
            )
            return lines | protected
        return lines

    def _get_reward_values_discount(self, reward, coupon, **kwargs):
        # Línea de recompensa del core, salvo sobre precios fijos y colorantes. Las tarjetas de
        # regalo y eWallet son un medio de pago: cubren el total del pedido.
        order = self
        if not reward.program_id.is_payment_program:
            order = self.with_context(exclude_protected_discount_lines=True)
        return super(SaleOrder, order)._get_reward_values_discount(reward, coupon, **kwargs)

    def _get_reward_values_product(self, reward, coupon, product=None, **kwargs):
        # Línea de recompensa del core, en su propia línea aunque el producto ya esté en el pedido.
        values_list = super()._get_reward_values_product(reward, coupon, product=product, **kwargs)
        for values in values_list:
            values.update({
                'pricing_rule_type': 'promo',
                'pricing_rule_origin': f"Promoción: {reward.program_id.name}",
                'pricing_rule_id': False,
            })
        return values_list

    def _get_claimable_rewards(self, forced_coupons=None):
        # Las promociones por línea se eligen en su propio wizard, no consumen puntos del cupón.
        result = super()._get_claimable_rewards(forced_coupons=forced_coupons)
        for coupon, rewards in list(result.items()):
            result[coupon] = rewards.filtered(lambda reward: not reward._is_line_promotion())
            if not result[coupon]:
                result.pop(coupon)
        return result
    
    @api.depends('order_line.promo_available_count')
    def _compute_promo_available_line_count(self):
        for order in self:
            order.promo_available_line_count = len(order.order_line.filtered('promo_available_count'))

    def _get_line_promotion_points(self, program):
        """Puntos que el pedido genera en `program`, o `None` si no cumple ninguna de sus reglas.

        Reproduce `_program_check_compute_points` del core, salvo el mínimo: una línea en promoción
        cuenta sin ese descuento. Solo aplica a programas de pedido actual, sin puntos divididos.
        """
        self.ensure_one()
        lines = self.order_line.filtered(lambda line: not line.combo_item_id)
        qty_lines = lines.filtered(lambda line: line.product_id and not line.reward_id)
        products = self.order_line.product_id
        products_per_rule = program._get_valid_products(products)
        points = None
        for rule in program.rule_ids:
            if rule.mode == 'with_code' and rule not in self.code_enabled_rule_ids:
                continue
            rule_products = products_per_rule.get(rule, products.browse())
            # Las recompensas automáticas y las del propio programa no cuentan para su mínimo.
            amount_lines = lines.filtered(
                lambda line: line.product_id in rule_products and not (
                    line.reward_id.reward_type == 'discount'
                    and (line.reward_id.program_id.trigger == 'auto' or line.reward_id.program_id == program)
                )
            )
            amounts = [
                price_line._get_line_promotion_threshold_amounts()
                for price_line in amount_lines._get_lines_with_price_batch()
            ]
            precision = self.currency_id.decimal_places or 2
            untaxed = float_round(sum(amount[0] for amount in amounts), precision_digits=precision)
            taxed = float_round(sum(amount[1] for amount in amounts), precision_digits=precision)
            minimum = rule._compute_amount(self.currency_id)
            if minimum > (taxed if rule.minimum_amount_tax_mode == 'incl' else untaxed):
                continue
            qty = sum(qty_lines.filtered(lambda line: line.product_id in rule_products).mapped('product_uom_qty'))
            if not rule_products or qty < rule.minimum_qty:
                continue
            points = points or 0.0
            if rule.reward_point_mode == 'order':
                points += rule.reward_point_amount
            elif rule.reward_point_mode == 'unit':
                points += rule.reward_point_amount * qty
            elif rule.reward_point_mode == 'money':
                points += float_round(rule.reward_point_amount * taxed, precision_digits=2, rounding_method='DOWN')
        return points

    def _get_line_promotion_rewards(self):
        """Recompensas que cada línea del pedido puede elegir: {línea: loyalty.reward}."""
        self.ensure_one()
        no_reward = self.env['loyalty.reward']
        result = {line: no_reward for line in self.order_line}
        eligible_lines = self.order_line.filtered(lambda line: line._admits_line_promotion())
        if not eligible_lines:
            return result
        applied_programs = (self.order_line.promo_reward_id.program_id | self.order_line.reward_id.program_id)
        programs = self.env['loyalty.program'].search([
            *self._get_program_domain(),
            ('program_type', 'in', LINE_PROMOTION_PROGRAM_TYPES),
        ])
        for program in programs:
            prog_sudo = program.sudo()
            if prog_sudo.limit_usage and prog_sudo.total_order_count >= prog_sudo.max_usage and program not in applied_programs:
                continue
            points = self._get_line_promotion_points(program)
            if points is None:
                continue
            for reward in program.reward_ids:
                if not reward._is_line_promotion() or points < reward.required_points:
                    continue
                reward_lines = eligible_lines
                if reward.discount_applicability == 'specific':
                    domain = reward._get_discount_product_domain()
                    reward_lines = eligible_lines.filtered(lambda line: line.product_id.filtered_domain(domain))
                for line in reward_lines:
                    result[line] |= reward
        return result

    def _get_line_promotion_loss_reason(self, line, reward):
        self.ensure_one()
        if not reward.active or not reward.program_id.filtered_domain(self._get_program_domain()):
            return _("ya no está vigente")
        if not line._admits_line_promotion():
            return _("la línea ya no la admite")
        if reward.discount_applicability == 'specific' and not line.product_id.filtered_domain(
            reward._get_discount_product_domain()
        ):
            return _("el producto ya no la admite")
        return _("el pedido ya no cumple la condición")

    def _reevaluate_line_promotions(self):
        """Quita las promociones por línea que ya no proceden y actualiza el % de las vigentes."""
        digits = self.env['decimal.precision'].precision_get('Discount')
        for order in self.filtered(lambda order: order.state in ('draft', 'sent')):
            orphaned = order.order_line.filtered(lambda l: not l.promo_reward_id and l.pricing_rule_type == 'promo')
            if orphaned:
                orphaned._drop_line_promotion()
            promo_lines = order.order_line.filtered('promo_reward_id')
            notices = []
            # Al volver al acuerdo, una línea puede dejar a otro programa debajo de su mínimo.
            while promo_lines:
                available = order._get_line_promotion_rewards()
                lost_lines = promo_lines.filtered(lambda line: line.promo_reward_id not in available[line])
                if not lost_lines:
                    break
                for line in lost_lines:
                    reward = line.promo_reward_id
                    reason = order._get_line_promotion_loss_reason(line, reward)
                    line._drop_line_promotion()
                    notices.append(_(
                        "La línea %(product)s dejó la promoción %(program)s: %(reason)s. Volvió a %(origin)s.",
                        product=line.product_id.display_name,
                        program=reward.program_id.name,
                        reason=reason,
                        origin=line.pricing_rule_origin or _("sin descuento"),
                    ))
                promo_lines -= lost_lines
            for line in promo_lines:
                reward = line.promo_reward_id
                values = line._line_promotion_values(reward)
                if float_compare(line.discount, reward.discount, precision_digits=digits):
                    notices.append(_(
                        "La línea %(product)s cambió su promoción %(program)s a %(discount)s %%.",
                        product=line.product_id.display_name,
                        program=reward.program_id.name,
                        discount=reward.discount,
                    ))
                elif line.pricing_rule_origin == values['pricing_rule_origin']:
                    continue
                line.with_context(skip_manual_discount_breakdown=True).write(values)
            order._notify_promotion_changes(notices)

    def _notify_promotion_changes(self, notices):
        self.ensure_one()
        if not notices:
            return
        self.message_post(body=Markup('<br/>').join(notices))
        self.env.user._bus_send('simple_notification', {
            'type': 'warning',
            'title': _("Promociones de %s", self.name),
            'message': '\n'.join(notices),
            'sticky': True,
        })

    def _gift_reward_lines(self):
        return self.order_line.filtered(lambda line: line.reward_id.reward_type == 'product')

    def _program_check_compute_points(self, programs):
        # El core evalúa el umbral mínimo usando el neto de la línea. Para líneas con promoción
        # por línea, se evalúa el importe antes de dicha promoción para que la validación
        # de compra mínima se comporte de manera consistente independientemente del orden de aplicación.
        lines_with_promo = self.order_line.filtered('promo_reward_id')
        if not lines_with_promo:
            return super()._program_check_compute_points(programs)
        saved = {}
        for line in lines_with_promo:
            untaxed, taxed = line._get_line_promotion_threshold_amounts()
            saved[line] = (line.price_subtotal, line.price_total, line.price_tax)
            line.price_subtotal = untaxed
            line.price_total = taxed
            line.price_tax = taxed - untaxed
        try:
            return super()._program_check_compute_points(programs)
        finally:
            for line, (subtotal, total, tax) in saved.items():
                line.price_subtotal = subtotal
                line.price_total = total
                line.price_tax = tax

    def _update_programs_and_rewards(self):
        # El core mide el mínimo con el neto: quitaría el código de una promoción por línea que lo
        # cumple sin su propio descuento. Lo decide la reevaluación, que lo mide sin él.
        line_rules = self.code_enabled_rule_ids.filtered(
            lambda rule: any(reward._is_line_promotion() for reward in rule.program_id.reward_ids)
        )
        # Restaurar % de promociones antes de que el core evalúe puntos y recompensas
        for order in self:
            for line in order.order_line.filtered('promo_reward_id'):
                if line.promo_reward_id.discount and line.discount != line.promo_reward_id.discount:
                    line.with_context(skip_manual_discount_breakdown=True).write({
                        'discount': line.promo_reward_id.discount,
                        'technical_discount': line.promo_reward_id.discount,
                    })
        # El core puede reescribir o recrear las líneas de regalo: se compara por recompensa.
        gifts_before = defaultdict(lambda: self.env['product.product'])
        for line in self._gift_reward_lines():
            gifts_before[line.reward_id] |= line.product_id
        # El core reescribe `order_line` a mitad del recorrido: sin el contexto, `write` lanzaría
        # otra reevaluación dentro de esta y la exterior borraría las promociones y recompensas siguientes.
        res = super(SaleOrder, self.with_context(gift_reevaluation=True, in_loyalty_update=True))._update_programs_and_rewards()
        # El core pone el precio de las recompensas en 0 y reescribe el regalo sin precio: con la
        # misma cantidad no se recalcula, y el regalo quedaría a 0 con su 100 %.
        gifts = self._gift_reward_lines()
        gifts.with_context(force_price_recomputation=True)._compute_price_unit()
        remaining_rewards = gifts.reward_id
        self._notify_promotion_changes([
            _(
                "Se retiró el regalo %(product)s de la promoción %(program)s: ya no se cumple la condición.",
                product=product.display_name,
                program=reward.program_id.name,
            )
            for reward, products in gifts_before.items() if reward not in remaining_rewards
            for product in products
        ])
        removed_rules = line_rules - self.code_enabled_rule_ids
        if removed_rules:
            self.code_enabled_rule_ids |= removed_rules
        self._reevaluate_line_promotions()
        unmet_rules = line_rules.filtered(
            lambda rule: self._get_line_promotion_points(rule.program_id) is None
        )
        if unmet_rules:
            self.code_enabled_rule_ids -= unmet_rules
        return res

    def _apply_best_line_promotions(self):
        """Aplica a cada línea su promoción de mayor %, solo si supera su descuento actual.
        Devuelve las líneas que cambiaron."""
        self.ensure_one()
        digits = self.env['decimal.precision'].precision_get('Discount')
        lines_by_reward = defaultdict(lambda: self.env['sale.order.line'])
        for line, rewards in self._get_line_promotion_rewards().items():
            if not rewards:
                continue
            best = rewards.sorted(key=lambda reward: (-reward.discount, reward.id))[0]
            if best != line.promo_reward_id and float_compare(best.discount, line.discount, precision_digits=digits) > 0:
                lines_by_reward[best] |= line
        for reward, lines in lines_by_reward.items():
            lines._apply_line_promotion(reward)
        changed = self.env['sale.order.line'].union(*lines_by_reward.values())
        self._reevaluate_line_promotions()
        return changed

    def _update_order_line_info(self, product_id, quantity, **kwargs):
        # El Catálogo escribe o borra la línea sin pasar por `write`: se reevalúa como al guardar.
        res = super()._update_order_line_info(product_id, quantity, **kwargs)
        if self.state not in ('draft', 'sent'):
            return res
        self._reevaluate_line_promotions()
        if self._gift_reward_lines():
            self._update_programs_and_rewards()
        # El core calculó el precio de la tarjeta antes de reevaluar.
        section_id = kwargs.get('section_id', False)
        line = self.order_line.filtered(
            lambda line: line.product_id.id == product_id and line.get_parent_section_line().id == section_id
        )
        return line._get_discounted_price() if len(line) == 1 else res

    def action_open_reward_wizard(self):
        # «Promociones»: aplica en bloque las promociones por línea que mejoran el descuento y
        # después ofrece las demás recompensas en el wizard nativo.
        self.ensure_one()
        if self.state not in ('draft', 'sent'):
            return super().action_open_reward_wizard()
        self._update_programs_and_rewards()
        if not any(self._get_line_promotion_rewards().values()):
            return super().action_open_reward_wizard()
        changed = self._apply_best_line_promotions()
        if self._get_claimable_rewards():
            action = self.env['ir.actions.actions']._for_xml_id('sale_loyalty.sale_loyalty_reward_wizard_action')
            action['context'] = {'active_id': self.id}
            return action
        if changed:
            message = _("Se aplicó la mejor promoción en %s línea(s).", len(changed))
        else:
            message = _("Ninguna promoción mejora el descuento actual de las líneas.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success' if changed else 'info',
                'title': _("Promociones"),
                'message': message,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_confirm(self):
        self._check_stock_availability()
        return super().action_confirm()

    def _check_stock_availability(self):
        for order in self:
            insufficient_lines = []
            quantities = {}
            for line in order.order_lines_for_stock_check():
                pending = max(line.product_uom_qty - line.qty_delivered, 0)
                if pending <= 0:
                    continue
                product = line.product_id
                quantities[product] = quantities.get(product, 0) + line.product_uom_id._compute_quantity(
                    pending, product.uom_id, round=False)
            for product, quantity in quantities.items():
                available = product.with_company(order.company_id).with_context(
                    warehouse_id=order.warehouse_id.id, warehouse=order.warehouse_id.id
                ).free_qty
                if available <= 0 or product.uom_id.compare(quantity, available) > 0:
                    insufficient_lines.append(order._stock_shortage_details(product, quantity, available))
            if insufficient_lines:
                raise UserError(_(
                    "No se puede confirmar la orden: no hay inventario suficiente "
                    "para los siguientes productos:\n\n%s"
                ) % '\n'.join(insufficient_lines))

    def _stock_shortage_details(self, product, quantity, available):
        self.ensure_one()
        details = [_('%(product)s — almacén %(warehouse)s: solicitado %(qty)s %(unit)s, disponible %(available)s %(unit)s') % {
            'product': product.display_name, 'warehouse': self.warehouse_id.display_name,
            'qty': quantity, 'unit': product.uom_id.name, 'available': available}]
        alternatives = []
        for warehouse in self.env['stock.warehouse'].search([
                ('company_id', '=', self.company_id.id), ('id', '!=', self.warehouse_id.id)], order='name, id'):
            stock = product.with_company(self.company_id).with_context(
                warehouse_id=warehouse.id, warehouse=warehouse.id).free_qty
            if product.uom_id.compare(stock, 0) > 0:
                coverage = _('cubre lo solicitado') if product.uom_id.compare(stock, quantity) >= 0 else _('disponibilidad parcial')
                alternatives.append('• %s: %s %s (%s)' % (warehouse.display_name, stock, product.uom_id.name, coverage))
        if alternatives:
            details.append(_('Disponibilidad en otras sucursales:'))
            details.extend(alternatives)
        else:
            details.append(_('Sin stock disponible en otras sucursales accesibles de esta compañía.'))
        return '\n'.join(details)

    def order_lines_for_stock_check(self):
        self.ensure_one()
        return self.order_line.filtered(
            lambda l: l.product_id.is_storable and not l.display_type
        )
    
    def _create_invoices(self, grouped=False, final=False, date=None):
        moves = super()._create_invoices(grouped=grouped, final=final, date=date)
        if not self.env.context.get('sale_auto_invoice_checkout'):
            moves.filtered(lambda move: move.state == 'draft' and not move._is_sale_credit_invoice()
                           and (not move.company_id.advance_risk_enabled or move.advance_source_ids)).action_post()
        return moves
