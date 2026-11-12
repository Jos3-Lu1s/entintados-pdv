# -*- coding: utf-8 -*-

from odoo import models

LINE_PROMOTION_PROGRAM_TYPES = ('promotion', 'promo_code')
LINE_PROMOTION_APPLICABILITIES = ('order', 'specific')


class LoyaltyReward(models.Model):
    _inherit = 'loyalty.reward'

    def unlink(self):
        # Si la recompensa está referenciada por líneas de pedido activas, se archiva en lugar
        # de eliminarse físicamente para mantener la integridad de los descuentos aplicados.
        rewards_in_use = self.filtered(
            lambda r: self.env['sale.order.line'].sudo().search_count([('promo_reward_id', '=', r.id)], limit=1)
        )
        if rewards_in_use:
            rewards_in_use.action_archive()
            remaining = self - rewards_in_use
            if not remaining:
                return True
            return super(LoyaltyReward, remaining).unlink()
        return super().unlink()

    def _is_line_promotion(self):
        """Recompensa que se elige por línea y vive en su `discount`, sin línea de recompensa.

        `cheapest` queda fuera porque el core descuenta una sola unidad, y un tope porque no se
        puede expresar como un % fijo.
        """
        self.ensure_one()
        return bool(
            self.reward_type == 'discount'
            and self.discount_mode == 'percent'
            and self.discount_applicability in LINE_PROMOTION_APPLICABILITIES
            and not self.discount_max_amount
            and self.program_id.program_type in LINE_PROMOTION_PROGRAM_TYPES
        )


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    def unlink(self):
        # Si el programa se elimina (directamente o tras archivarse), se retira la promoción
        # de cualquier línea de cotización activa para que vuelva a su acuerdo vigente
        # antes de que la cascada de base de datos elimine las recompensas.
        rewards = self.with_context(active_test=False).reward_ids
        lines_with_promo = self.env['sale.order.line'].sudo().search([
            ('promo_reward_id', 'in', rewards.ids),
            ('order_id.state', 'in', ('draft', 'sent')),
        ])
        if lines_with_promo:
            lines_with_promo._drop_line_promotion()
        return super().unlink()

    def _compute_order_count(self):
        # Reemplaza al core para contar una sola vez el pedido que tiene líneas de recompensa
        # nativas y líneas con promoción del mismo programa.
        reward_ids = self.reward_ids.ids
        groups = self.env['sale.order.line']._read_group(
            ['|', ('reward_id', 'in', reward_ids), ('promo_reward_id', 'in', reward_ids)],
            ['order_id'],
            ['reward_id:array_agg', 'promo_reward_id:array_agg'],
        )
        for program in self:
            program_rewards = set(program.reward_ids.ids)
            program.order_count = sum(
                bool(program_rewards.intersection(native + promo))
                for __, native, promo in groups
            )
