# -*- coding: utf-8 -*-

from odoo import api, models


class PosSession(models.Model):
    _inherit = 'pos.session'

    @api.model
    def _load_pos_data_models(self, config):
        models_to_load = super()._load_pos_data_models(config)

        tint_models = [
            'tint.size',
            'tint.base.type',
            'tint.base.capacity',
            # La galería es el primer nivel del filtrado en caja, así que va
            # antes que las fórmulas que la referencian.
            'tint.gallery',
            'tint.color',
            'tint.color.formula',
            'tint.color.formula.line',
        ]

        for model_name in tint_models:
            if model_name not in models_to_load:
                models_to_load.append(model_name)

        return models_to_load
