# -*- coding: utf-8 -*-

import ast
from collections import Counter

from odoo import api, fields, models

LINE_PROMOTION_PROGRAM_TYPES = ('promotion',)
LINE_PROMOTION_APPLICABILITIES = ('order', 'specific')


class LoyaltyReward(models.Model):
    _inherit = 'loyalty.reward'

    lines_product_id = fields.Many2one(
        'lines.product',
        string='Línea de producto',
        ondelete='restrict',
        help="Aplica el descuento a los productos pertenecientes a esta línea.",
    )
    scheme_id = fields.Many2one(
        'tint.schema',
        string='Esquema',
        ondelete='restrict',
        help="Aplica el descuento a los productos pertenecientes a este esquema.",
    )

    @api.onchange('lines_product_id')
    def _onchange_lines_product_id(self):
        if self.lines_product_id:
            self.scheme_id = self.lines_product_id.scheme
        self._sync_domain_from_line_or_scheme()

    @api.onchange('scheme_id')
    def _onchange_scheme_id(self):
        if self.lines_product_id and self.lines_product_id.scheme != self.scheme_id:
            self.lines_product_id = False
        self._sync_domain_from_line_or_scheme()

    def _sync_domain_from_line_or_scheme(self):
        """Sincroniza discount_product_domain a partir de la línea o esquema configurado."""
        if self.lines_product_id:
            line_id = self.lines_product_id._origin.id or self.lines_product_id.id
            self.discount_product_domain = f"[('lines_product_id', 'in', [{line_id}])]"
        elif self.scheme_id:
            scheme_id = self.scheme_id._origin.id or self.scheme_id.id
            self.discount_product_domain = f"[('scheme_id', 'in', [{scheme_id}])]"
        elif self.discount_product_domain and (
            'lines_product_id' in self.discount_product_domain or 'scheme_id' in self.discount_product_domain
        ):
            self.discount_product_domain = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('lines_product_id') and not vals.get('scheme_id'):
                line = self.env['lines.product'].browse(vals['lines_product_id'])
                if line.exists() and line.scheme:
                    vals['scheme_id'] = line.scheme.id
            if 'discount_product_domain' not in vals:
                if vals.get('lines_product_id'):
                    vals['discount_product_domain'] = f"[('lines_product_id', 'in', [{vals['lines_product_id']}])]"
                elif vals.get('scheme_id'):
                    vals['discount_product_domain'] = f"[('scheme_id', 'in', [{vals['scheme_id']}])]"
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('lines_product_id') and 'scheme_id' not in vals:
            line = self.env['lines.product'].browse(vals['lines_product_id'])
            if line.exists() and line.scheme:
                vals['scheme_id'] = line.scheme.id
        elif vals.get('scheme_id') and 'lines_product_id' not in vals:
            for record in self:
                if record.lines_product_id and record.lines_product_id.scheme.id != vals['scheme_id']:
                    vals['lines_product_id'] = False
        res = super().write(vals)
        if 'discount_product_domain' not in vals and ('lines_product_id' in vals or 'scheme_id' in vals):
            for record in self:
                record._sync_domain_from_line_or_scheme()
        return res

    def _get_reward_target_label(self):
        """Devuelve la etiqueta descriptiva del objetivo de la recompensa."""
        self.ensure_one()
        if self.discount_applicability == 'order':
            return ""
        if self.lines_product_id:
            return f"Línea: {self.lines_product_id.name}"
        if self.scheme_id:
            return f"Esquema: {self.scheme_id.name}"
        if self.discount_product_domain:
            try:
                domain = ast.literal_eval(self.discount_product_domain)
                for leaf in domain:
                    if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
                        field, op, val = leaf
                        if field == 'lines_product_id' and op in ('in', '=') and val:
                            lid = val[0] if isinstance(val, (list, tuple)) else val
                            line = self.env['lines.product'].browse(lid)
                            if line.exists():
                                return f"Línea: {line.name}"
                        elif field == 'scheme_id' and op in ('in', '=') and val:
                            sid = val[0] if isinstance(val, (list, tuple)) else val
                            scheme = self.env['tint.schema'].browse(sid)
                            if scheme.exists():
                                return f"Esquema: {scheme.name}"
            except Exception:
                pass
        if self.discount_product_ids:
            if len(self.discount_product_ids) == 1:
                return f"Producto: {self.discount_product_ids[0].name}"
            return f"{len(self.discount_product_ids)} productos"
        if self.discount_product_category_id:
            return f"Categoría: {self.discount_product_category_id.name}"
        if self.discount_product_tag_id:
            return f"Etiqueta: {self.discount_product_tag_id.name}"
        return ""

    def unlink(self):
        # Si la recompensa está referenciada por líneas de pedido activas, se archiva en lugar
        # de eliminarse físicamente para mantener la integridad de los descuentos aplicados.
        used_ids = {
            reward.id
            for model, field in (('sale.order.line', 'promo_reward_id'), ('sale.order', 'order_promo_reward_id'))
            for (reward,) in self.env[model].sudo()._read_group([(field, 'in', self.ids)], [field])
        }
        rewards_in_use = self.filtered(lambda r: r.id in used_ids)
        rewards_in_use.action_archive()
        remaining = self - rewards_in_use
        return super(LoyaltyReward, remaining).unlink() if remaining else True

    def _sorted_by_line_promotion_discount(self):
        """Mayor % primero: el orden con el que se elige y se ofrece una promoción por línea."""
        return self.sorted(key=lambda reward: (-reward.discount, reward.id))

    def _is_line_promotion(self):
        """Recompensa que se elige por línea y vive en su `discount`, sin línea de recompensa.

        `cheapest` queda fuera porque el core descuenta una sola unidad, y un tope porque no se
        puede expresar como un % fijo.
        """
        self.ensure_one()
        return (
            self.reward_type == 'discount'
            and self.discount_mode == 'percent'
            and self.discount_applicability in LINE_PROMOTION_APPLICABILITIES
            and not self.discount_max_amount
            and self.program_id.program_type in LINE_PROMOTION_PROGRAM_TYPES
        )

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        extra = ['lines_product_id', 'scheme_id']
        for field in extra:
            if field not in fields:
                fields.append(field)
        return fields


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    def unlink(self):
        # Si el programa se elimina (directamente o tras archivarse), se retira la promoción
        # de cualquier línea de cotización activa para que vuelva a su acuerdo vigente
        # antes de que la cascada de base de datos elimine las recompensas.
        rewards = self.with_context(active_test=False).reward_ids
        self.env['sale.order.line'].sudo().search([
            ('promo_reward_id', 'in', rewards.ids),
            ('order_id.state', 'in', ('draft', 'sent')),
        ])._drop_line_promotion()
        self.env['sale.order'].sudo().search([
            ('order_promo_reward_id', 'in', rewards.ids),
            ('state', 'in', ('draft', 'sent')),
        ])._clear_order_promotion()
        return super().unlink()

    def _compute_order_count(self):
        # Reemplaza al core para contar una sola vez el pedido que tiene líneas de recompensa
        # nativas y líneas con promoción del mismo programa. Se cuenta con sudo: el límite de usos
        # es global y las reglas de "solo sus documentos" ocultarían los pedidos de otros vendedores.
        reward_ids = self.reward_ids.ids
        groups = self.env['sale.order.line'].sudo()._read_group(
            ['|', ('reward_id', 'in', reward_ids), ('promo_reward_id', 'in', reward_ids)],
            ['order_id'],
            ['reward_id:array_agg', 'promo_reward_id:array_agg'],
        )
        program_by_reward = {reward.id: reward.program_id.id for reward in self.reward_ids}
        counts = Counter()
        for __, native, promo in groups:
            counts.update({program_by_reward[rid] for rid in native + promo if rid in program_by_reward})

        if 'pos.order.line' in self.env:
            pos_domain = [
                '|', ('reward_id', 'in', reward_ids), ('promo_reward_id', 'in', reward_ids),
                ('order_id.state', 'in', ('paid', 'done', 'invoiced')),
            ]
            pos_groups = self.env['pos.order.line'].sudo()._read_group(
                pos_domain,
                ['order_id'],
                ['reward_id:array_agg', 'promo_reward_id:array_agg'],
            )
            for __, native, promo in pos_groups:
                counts.update({program_by_reward[rid] for rid in native + promo if rid in program_by_reward})

        for program in self:
            program.order_count = counts[program.id]
