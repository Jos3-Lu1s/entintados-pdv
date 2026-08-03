# -*- coding: utf-8 -*-

from odoo import api, fields, models


class TintSize(models.Model):
    _name = 'tint.size'
    _description = "Presentación de envase de pintura"
    _order = 'sequence, volume_liters, id'
    _inherit = ['pos.load.mixin']

    name = fields.Char(
        string="Presentación", required=True, translate=True)
    code = fields.Char(
        string="Código", required=True,
        help="Código corto usado por la operación: L = Litro, G = Galón, Q = Cubeta.")
    volume_liters = fields.Float(
        string="Volumen (L)", required=True, digits=(12, 3),
        help="Volumen nominal del envase en litros. Se usa para verificar "
             "la consistencia de la matriz de capacidad de colorante.")
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)

    capacity_ids = fields.One2many(
        comodel_name='tint.base.capacity', inverse_name='size_id',
        string="Capacidades por tipo de base")
    capacity_count = fields.Integer(
        string="Capacidades definidas", compute='_compute_capacity_count')

    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        "Ya existe una presentación con ese código.",
    )
    _volume_positive = models.Constraint(
        'CHECK (volume_liters > 0)',
        "El volumen del envase debe ser mayor que cero.",
    )

    @api.depends('capacity_ids')
    def _compute_capacity_count(self):
        data = self.env['tint.base.capacity']._read_group(
            domain=[('size_id', 'in', self.ids)],
            groupby=['size_id'],
            aggregates=['__count'],
        )
        counts = {size.id: count for size, count in data}
        for size in self:
            size.capacity_count = counts.get(size.id, 0)

    @api.depends('name', 'code')
    def _compute_display_name(self):
        for size in self:
            size.display_name = "%s (%s)" % (size.name, size.code) if size.code else size.name

    # --- Normalización del código ---------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if isinstance(vals.get('code'), str):
                vals['code'] = vals['code'].strip()
        return super().create(vals_list)

    def write(self, vals):
        if isinstance(vals.get('code'), str):
            vals['code'] = vals['code'].strip()
        return super().write(vals)

    @api.onchange('code')
    def _onchange_code_trim(self):
        if self.code and isinstance(self.code, str):
            self.code = self.code.strip()

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name', 'code', 'volume_liters', 'sequence']

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('active', '=', True)]
