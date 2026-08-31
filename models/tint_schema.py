# -*- coding: utf-8 -*-

from odoo import api, fields, models


class TintSchema(models.Model):
    _name = 'tint.schema'
    _description = "Esquemas de las bases"
    _inherit = ['pos.load.mixin']

    name = fields.Char(
        string="Nombre", required=True, translate=True,
        help="Nombre del esquema para la base.")

    line_ids = fields.One2many(
        comodel_name='lines.product',
        inverse_name='scheme',
        string='Líneas de producto',
    )

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name']

    @api.model
    def _load_pos_data_domain(self, data, config):
        return []