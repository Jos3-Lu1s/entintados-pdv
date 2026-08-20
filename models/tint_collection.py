# -*- coding: utf-8 -*-

from odoo import api, fields, models


class TintCollection(models.Model):
    _name = 'tint.collection'
    _description = "Colección o carta de color"
    _order = 'sequence, name, id'
    _inherit = ['pos.load.mixin', 'tint.code.mixin']

    name = fields.Char(
        string="Colección", required=True, translate=True,
        help="Nombre de la carta o colección de color.")
    code = fields.Char(
        help="Código corto para identificar la colección en listados y cartas.")
    description = fields.Html(
        string="Descripción", translate=True, sanitize=True,
        help="Nota interna sobre el alcance o el uso de esta colección.")
    sequence = fields.Integer(
        string="Secuencia", default=10,
        help="Orden en que se muestra la colección en listados y en caja.")
    active = fields.Boolean(
        string="Activo", default=True,
        help="Si se desmarca, la colección se archiva y deja de ofrecerse.")

    color_ids = fields.One2many(
        comodel_name='tint.color', inverse_name='collection_id',
        string="Colores",
        help="Colores que forman parte de esta colección o carta.")
    color_count = fields.Integer(
        string="Colores", compute='_compute_color_count',
        help="Número de colores incluidos en esta colección.")

    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        "Ya existe una colección con ese código.",
    )

    @api.depends('color_ids')
    def _compute_color_count(self):
        data = self.env['tint.color']._read_group(
            domain=[('collection_id', 'in', self.ids)],
            groupby=['collection_id'],
            aggregates=['__count'],
        )
        counts = {collection.id: count for collection, count in data}
        for collection in self:
            collection.color_count = counts.get(collection.id, 0)

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name', 'code', 'sequence']

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('active', '=', True)]
