# -*- coding: utf-8 -*-

from odoo import api, fields, models


class TintCodeMixin(models.AbstractModel):
    """Campo `code` corto, obligatorio y normalizado.

    Centraliza el comportamiento común de los códigos que identifican a las
    entidades del catálogo: son obligatorios y se guardan sin espacios sobrantes.
    """

    _name = 'tint.code.mixin'
    _description = "Código corto normalizado"

    code = fields.Char(string="Código", required=True)

    @staticmethod
    def _normalize_code(code):
        return code.strip().upper()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if isinstance(vals.get('code'), str):
                vals['code'] = self._normalize_code(vals['code'])
        return super().create(vals_list)

    def write(self, vals):
        if isinstance(vals.get('code'), str):
            vals['code'] = self._normalize_code(vals['code'])
        return super().write(vals)

    @api.onchange('code')
    def _onchange_code_normalize(self):
        if self.code:
            self.code = self._normalize_code(self.code)
