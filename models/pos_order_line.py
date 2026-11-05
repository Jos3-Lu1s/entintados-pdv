# -*- coding: utf-8 -*-

import re

from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    tint_color_code = fields.Char(string='Código del color entintado', readonly=True, copy=False)

    def _tint_invoice_description(self):
        """Return the commercial base + color label and any unrelated customer notes."""
        self.ensure_one()
        recipe = self.sale_order_line_id.tint_snapshot or {}
        if self.product_id.tint_role != 'base' or not (
                self.tint_color_code or recipe or
                self.combo_line_ids.filtered(lambda line: line.product_id.tint_role == 'colorant')):
            return '', self.customer_note
        color_note = ''
        notes = []
        for part in (self.customer_note or '').splitlines():
            match = re.match(r'^\s*(?:Color:|\+)\s*(.*)$', part)
            if match and not color_note:
                color_note = match[1].strip()
            else:
                notes.append(part)
        code = recipe.get('color_code') or self.tint_color_code or ''
        name = recipe.get('color_name') or color_note
        match = re.match(r'^\[([^\]]+)\]\s*(.*)$', name)
        if match:
            code = code or match[1]
            name = match[2]
        if not name and code:
            colors = self.env['tint.color'].search([('code', '=', code)], limit=2)
            name = colors.name if len(colors) == 1 else ''
        color = ' '.join(filter(None, ['[%s]' % code if code else '', name]))
        if not color:
            return '', self.customer_note
        return '%s + %s' % (self.product_id.display_name, color), '\n'.join(notes).strip()

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
        default='none',
        readonly=True,
        copy=False,
    )
    pricing_rule_origin = fields.Char(
        string='Origen Acuerdo',
        readonly=True,
        copy=False,
    )
    pricing_rule_id = fields.Many2one(
        comodel_name='res.partner.discount.rule',
        string='Regla de Acuerdo',
        readonly=True,
        copy=False,
        ondelete='set null',
    )

    @api.model
    def _load_pos_data_fields(self, config):
        params = super()._load_pos_data_fields(config)
        params += ['pricing_rule_type', 'pricing_rule_origin', 'pricing_rule_id', 'tint_color_code']
        return params
