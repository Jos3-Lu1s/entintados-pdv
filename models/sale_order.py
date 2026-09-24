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
        res = super().write(vals)
        for order in self:
            if 'opportunity_id' in vals:
                order._sync_opportunity_stage()
            if 'partner_id' in vals and order.state in ('draft', 'sent'):
                order._recompute_pricing_rules()
            if vals.get('state') == 'sale' and order.state == 'sale':
                order._cancel_sibling_quotations()
                order._advance_opportunity_to_closed()
                if order.opportunity_id:
                    order._schedule_confirm_quotation_activity()
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

    def _recompute_pricing_rules(self):
        for order in self:
            lines_to_update = order.order_line.filtered(
                lambda l: l.product_id
                and not l.display_type
                and not getattr(l, 'is_reward_line', False)
            )
            if lines_to_update:
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
        self.activity_schedule(
            activity_type_id=activity_type.id,
            user_id=self.opportunity_id.user_id.id or self.env.uid,
            summary=activity_type.summary,
            note=note,
        ).action_feedback(feedback=note)
    def action_open_reward_wizard(self):
        self.ensure_one()
        self._update_programs_and_rewards()
        claimable_rewards = self._get_claimable_rewards()
        if not claimable_rewards:
            return True
        return self.env['ir.actions.actions']._for_xml_id('sale_loyalty.sale_loyalty_reward_wizard_action')

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
    