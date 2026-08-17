# -*- coding: utf-8 -*-

from odoo import api, fields, models


class TintSchema(models.Model):
    _name = 'tint.schema'
    _description = "Esquemas de las bases"
    """ _order = 'sequence, volume_liters, id'
    _inherit = ['pos.load.mixin', 'tint.code.mixin'] """
    name = fields.Char(
            string="Nombre", required=True, translate=True,
            help="Nombre del esquema para la base.")
 