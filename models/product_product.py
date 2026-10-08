# -*- coding: utf-8 -*-

from odoo import _, api, models
from odoo.exceptions import UserError, ValidationError

from ..utils.default_code import REQUIRE_DEFAULT_CODE, clean_default_code


class ProductProduct(models.Model):
    _inherit = 'product.product'

    # --- Referencia interna ---------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            clean_default_code(vals)
        if not self.env.context.get(REQUIRE_DEFAULT_CODE):
            return super().create(vals_list)
        products = super(
            ProductProduct, self.with_context(**{REQUIRE_DEFAULT_CODE: False})
        ).create(vals_list).with_env(self.env)
        products._check_default_code_required()
        return products

    def write(self, vals):
        clean_default_code(vals)
        if not self.env.context.get(REQUIRE_DEFAULT_CODE):
            return super().write(vals)
        result = super(
            ProductProduct, self.with_context(**{REQUIRE_DEFAULT_CODE: False})
        ).write(vals)
        self._check_default_code_required()
        return result

    def web_save(self, vals, specification, next_id=None):
        return super(
            ProductProduct, self.with_context(**{REQUIRE_DEFAULT_CODE: True})
        ).web_save(vals, specification, next_id=next_id)

    def web_save_multi(self, vals_list, specification):
        return super(
            ProductProduct, self.with_context(**{REQUIRE_DEFAULT_CODE: True})
        ).web_save_multi(vals_list, specification)

    def load(self, fields, data):
        # El ORM convierte la ValidationError de cada fila en un mensaje de
        # error y revierte el lote completo si hubo alguno.
        return super(
            ProductProduct, self.with_context(**{REQUIRE_DEFAULT_CODE: True})
        ).load(fields, data)

    @api.model
    def name_create(self, name):
        raise UserError(_(
            "Para crear un producto debe capturar su referencia interna. "
            "Use «Crear y editar…»."
        ))

    def _check_default_code_required(self):
        missing = self.filtered(lambda p: not p.default_code)
        if missing:
            raise ValidationError(_(
                "Debe capturar la referencia interna de los siguientes "
                "productos: %s.",
                ", ".join(missing.mapped('display_name')),
            ))
