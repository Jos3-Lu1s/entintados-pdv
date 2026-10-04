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
        if not reward.program_id.is_payment_program:
            self = self.with_context(exclude_protected_discount_lines=True)
        return super()._get_reward_values_discount(reward, coupon, **kwargs)

    