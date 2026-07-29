# -*- coding: utf-8 -*-

from odoo import api, fields, models

from ..utils.points import format_points


class TintBaseCapacity(models.Model):
    _name = 'tint.base.capacity'
    _description = "Capacidad de colorante por base y presentación"
    _order = 'base_type_id, size_id, id'

    base_type_id = fields.Many2one(
        comodel_name='tint.base.type', string="Tipo de base",
        required=True, ondelete='cascade', index=True)
    size_id = fields.Many2one(
        comodel_name='tint.size', string="Presentación",
        required=True, ondelete='cascade', index=True)

    max_points = fields.Integer(
        string="Colorante máximo (Pts.)", required=True,
        help="Cantidad máxima de colorante que admite el envase, en puntos. "
             "Una onza equivale a 48 puntos.")
    max_points_display = fields.Char(
        string="Colorante máximo", compute='_compute_max_points_display',
        help="La misma capacidad en la notación mixta de la operación, p. ej. 9Y 24.")

    theoretical_points = fields.Integer(
        string="Teórico (Pts.)", compute='_compute_theoretical_points', store=True,
        help="Puntos por litro del tipo de base multiplicados por el volumen "
             "de la presentación. Sirve para detectar capturas inconsistentes.")
    is_consistent = fields.Boolean(
        string="Consistente", compute='_compute_theoretical_points', store=True,
        help="Falso cuando la capacidad capturada no coincide con el valor "
             "teórico. Puede ser una excepción legítima del fabricante o un "
             "error de captura: revíselo.")

    volume_liters = fields.Float(
        string="Volumen (L)", related='size_id.volume_liters', readonly=True)
    points_per_liter = fields.Integer(
        string="Puntos por litro", related='base_type_id.points_per_liter', readonly=True)
    requires_extraction = fields.Boolean(
        related='base_type_id.requires_extraction', readonly=True)

    _base_size_uniq = models.Constraint(
        'UNIQUE(base_type_id, size_id)',
        "Ya existe una capacidad definida para esa combinación de tipo de base y presentación.",
    )
    _max_points_positive = models.Constraint(
        'CHECK (max_points > 0)',
        "La capacidad de colorante debe ser mayor que cero.",
    )

    @api.depends('max_points')
    def _compute_max_points_display(self):
        for capacity in self:
            capacity.max_points_display = format_points(capacity.max_points)

    @api.depends('max_points', 'base_type_id.points_per_liter', 'size_id.volume_liters')
    def _compute_theoretical_points(self):
        for capacity in self:
            theoretical = round(
                capacity.base_type_id.points_per_liter * capacity.size_id.volume_liters
            )
            capacity.theoretical_points = theoretical
            capacity.is_consistent = capacity.max_points == theoretical

    @api.depends('base_type_id', 'size_id', 'max_points')
    def _compute_display_name(self):
        for capacity in self:
            capacity.display_name = "%s / %s: %s" % (
                capacity.base_type_id.code or "",
                capacity.size_id.code or "",
                format_points(capacity.max_points),
            )
