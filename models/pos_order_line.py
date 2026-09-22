# -*- coding: utf-8 -*-

from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

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

    def _export_for_ui(self, orderline):
        result = super()._export_for_ui(orderline)
        result['pricing_rule_type'] = orderline.pricing_rule_type
        result['pricing_rule_origin'] = orderline.pricing_rule_origin
        result['pricing_rule_id'] = orderline.pricing_rule_id.id if orderline.pricing_rule_id else False
        return result

    @api.model
    def _load_pos_data_fields(self, config):
        params = super()._load_pos_data_fields(config)
        params += ['pricing_rule_type', 'pricing_rule_origin', 'pricing_rule_id']
        return params
