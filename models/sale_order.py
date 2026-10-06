from odoo import models, fields, api, _, Command
from odoo.exceptions import ValidationError, UserError
from collections import defaultdict
import logging
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
    # Solo para la vista: el bloqueo de Ajustes no se aplica en el servidor.
    price_unit_edit_locked = fields.Boolean(compute='_compute_edit_locked')
    discount_edit_locked = fields.Boolean(compute='_compute_edit_locked')

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
        orders = super().create(vals_list)
        for order in orders:
            order._sync_opportunity_stage()
        return orders

    def write(self, vals):
        if 'partner_id' in vals:
            self = self.with_context(force_price_recomputation=True)
        # Cambio de tarifa o de cliente (y con ellos de moneda) por código, RPC, importación o el
        # formulario: tras el write `_origin` ya trae la moneda nueva, así que las líneas que la
        # tarifa no recalcula se convierten aquí desde la anterior. El precio fijo congelado
        # también, salvo con recálculo forzado (cambio de cliente), que reaplica la regla vigente.
        force = self.env.context.get('force_price_recomputation')
        previous_prices = {}
        if 'pricelist_id' in vals or 'partner_id' in vals:
            previous_prices = {
                line: (line.currency_id, line.product_uom_id, line.price_unit, line.technical_price_unit)
                for line in self.order_line
                if line._keeps_price_on_currency_change()
                or (not force and line.pricing_rule_type == 'fixed_price' and not line.qty_invoiced)
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

    def _recompute_prices(self):
        # "Actualizar precios": el core pone discount = 0 y llama a _compute_discount() sin forzar;
        # forzado, se reaplica el acuerdo vigente en vez de releer ese 0 como acuerdo congelado.
        # Ese discount = 0 tampoco es un descuento manual, y el manual previo se restaura después.
        manual_lines = self.order_line.filtered(lambda line: line.pricing_rule_type == 'manual')
        manual_discounts = {line: line.discount for line in manual_lines}
        res = super(SaleOrder, self.with_context(
            force_price_recomputation=True, skip_manual_discount_breakdown=True,
        ))._recompute_prices()
        for line, discount in manual_discounts.items():
            if line.pricing_rule_type == 'manual' and line.discount != discount:
                line.with_context(skip_manual_discount_breakdown=True).write({
                    'discount': discount,
                    **line._manual_discount_values(discount),
                })
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
        self.ensure_one()
        if reward.reward_type != 'discount' or reward.discount_mode != 'percent':
            return super()._get_reward_values_discount(reward, coupon, **kwargs)

        if reward.discount_applicability == 'order':
            lines = self.order_line.filtered(
                lambda l: not l.display_type
                and not getattr(l, 'is_reward_line', False)
                and l.product_id
            )
        elif reward.discount_applicability == 'specific':
            lines = self._get_specific_discountable_lines(reward)
        elif reward.discount_applicability == 'cheapest':
            cheapest = self._cheapest_line(reward)
            lines = cheapest if cheapest else self.env['sale.order.line']
        else:
            lines = self.env['sale.order.line']

        lines_to_discount = lines.filtered(
            lambda l: l.product_id
            and (not getattr(l, 'is_tint_colorant', False) and getattr(l.product_id, 'tint_role', False) != 'colorant')
            and l.pricing_rule_type != 'fixed_price'
        )

        if not lines_to_discount:
            raise UserError(_("No hay productos aplicables para aplicar el descuento de la promoción."))

        discount = min(reward.discount, 100.0)
        origin_label = f"Promoción: {reward.program_id.name}"

        for line in lines_to_discount:
            line.discount = discount
            line.pricing_rule_type = 'promo'
            line.pricing_rule_origin = origin_label
            line.pricing_rule_id = False

        return []

    def _get_reward_values_product(self, reward, coupon, product=None, **kwargs):
        self.ensure_one()
        assert reward.reward_type == 'product'

        reward_products = reward.reward_product_ids
        product = product or reward_products[:1]
        if not product or product not in reward_products:
            raise UserError(_("Producto no válido para reclamar."))

        promo_origin = f"Promoción: {reward.program_id.name}"

        # Buscar si el producto ya existe en la orden (excluyendo colorantes y precios fijos)
        existing_line = self.order_line.filtered(
            lambda l: l.product_id == product
            and not l.display_type
            and (not getattr(l, 'is_tint_colorant', False) and getattr(l.product_id, 'tint_role', False) != 'colorant')
            and l.pricing_rule_type != 'fixed_price'
        )[:1]

        if existing_line:
            if existing_line.product_uom_qty <= 1.0:
                existing_line.discount = 100.0
                existing_line.pricing_rule_type = 'promo'
                existing_line.pricing_rule_origin = promo_origin
                existing_line.pricing_rule_id = False
                return []
            else:
                existing_line.product_uom_qty -= 1.0
                taxes = existing_line.tax_ids or existing_line.tax_id
                return [{
                    'product_id': product.id,
                    'product_uom_qty': 1.0,
                    'price_unit': existing_line.price_unit,
                    'discount': 100.0,
                    'pricing_rule_type': 'promo',
                    'pricing_rule_origin': promo_origin,
                    'pricing_rule_id': False,
                    'tax_ids': [Command.set(taxes.ids)],
                }]

        price_unit = product.with_company(self.company_id).lst_price
        taxes = self.fiscal_position_id.map_tax(product.taxes_id._filter_taxes_by_company(self.company_id))
        qty = reward.reward_product_qty or 1.0
        return [{
            'product_id': product.id,
            'product_uom_qty': qty,
            'price_unit': price_unit,
            'discount': 100.0,
            'pricing_rule_type': 'promo',
            'pricing_rule_origin': promo_origin,
            'pricing_rule_id': False,
            'tax_ids': [Command.set(taxes.ids)],
        }]

    def _get_reward_values_free_product(self, reward, coupon, **kwargs):
        return self._get_reward_values_product(reward, coupon, **kwargs)

    def _get_claimable_rewards(self, forced_coupons=None):
        result = super()._get_claimable_rewards(forced_coupons=forced_coupons)
        for coupon, rewards in list(result.items()):
            for reward in list(rewards):
                if reward.reward_type == 'discount' and not reward.program_id.is_payment_program:
                    promo_origin = f"Promoción: {reward.program_id.name}"
                    if any(l.pricing_rule_type == 'promo' and l.pricing_rule_origin == promo_origin for l in self.order_line):
                        result[coupon] -= reward
            if not result[coupon]:
                result.pop(coupon, None)
        return result
    
    def action_confirm(self):
        self._check_stock_availability()
        return super().action_confirm()

    def _check_stock_availability(self):
        for order in self:
            insufficient_lines = []
            for line in order.order_lines_for_stock_check():
                available = line.product_id.with_context(
                    warehouse=order.warehouse_id.id
                ).qty_available
                if line.product_uom_qty > available:
                    insufficient_lines.append(_(
                        '%(product)s: solicitado %(qty)s, disponible %(available)s'
                    ) % {
                        'product': line.product_id.display_name,
                        'qty': line.product_uom_qty,
                        'available': available,
                    })
            if insufficient_lines:
                raise UserError(_(
                    "No se puede confirmar la orden: no hay inventario suficiente "
                    "para los siguientes productos:\n\n%s"
                ) % '\n'.join(insufficient_lines))

    def order_lines_for_stock_check(self):
        self.ensure_one()
        return self.order_line.filtered(
            lambda l: l.product_id.is_storable and not l.display_type
        )