# -*- coding: utf-8 -*-

from odoo import _, models


class SaleLoyaltyCouponWizard(models.TransientModel):
    _inherit = 'sale.loyalty.coupon.wizard'

    def action_apply(self):
        # Un código de promoción por línea no tiene nada que elegir en el wizard nativo: se elige
        # con la estrella de cada línea.
        order = self.order_id
        enabled_rules = order.code_enabled_rule_ids
        action = super().action_apply()
        new_rewards = (order.code_enabled_rule_ids - enabled_rules).program_id.reward_ids
        if (
            order.state in ('draft', 'sent')
            and any(reward._is_line_promotion() for reward in new_rewards)
            and not order._get_claimable_rewards()
        ):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'success',
                    'title': _("Código aplicado"),
                    'message': _("Elige la promoción con la estrella de cada línea, o pulsa «Promociones» para aplicar la mejor."),
                    'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
                },
            }
        return action
