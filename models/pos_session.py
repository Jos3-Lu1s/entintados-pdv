# -*- coding: utf-8 -*-

from odoo import api, models


class PosSession(models.Model):
    _inherit = 'pos.session'

    @api.model
    def _load_pos_data_models(self, config):
        # EXTENDS point_of_sale: agrega el catálogo de entintado al POS.
        data = super()._load_pos_data_models(config)
        data += [
            'tint.size',
            'tint.base.type',
            'tint.base.capacity',
            'tint.collection',
            'tint.color',
            'tint.color.formula',
            'tint.color.formula.line',
        ]
        return data
