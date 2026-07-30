# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..utils.points import format_points


class TintColorFormulaLine(models.Model):
    _name = 'tint.color.formula.line'
    _description = "Dosis de colorante de una fórmula"
    _order = 'formula_id, sequence, id'

    formula_id = fields.Many2one(
        comodel_name='tint.color.formula', string="Fórmula",
        required=True, ondelete='cascade', index=True)
    colorant_id = fields.Many2one(
        comodel_name='product.product', string="Colorante",
        required=True, ondelete='restrict', index=True,
        domain="[('tint_role', '=', 'colorant')]")
    points = fields.Integer(
        string="Dosis (Pts.)", required=True, default=1,
        help="Puntos de colorante a dispensar. Una onza equivale a 48 puntos.")
    points_display = fields.Char(
        string="Dosis", compute='_compute_points_display')
    colorant_slot = fields.Char(
        string="Posición", related='colorant_id.colorant_slot', readonly=True)
    sequence = fields.Integer(string="Secuencia", default=10)

    _points_positive = models.Constraint(
        'CHECK (points > 0)',
        "La dosis de colorante debe ser mayor que cero.",
    )
    _colorant_uniq = models.Constraint(
        'UNIQUE(formula_id, colorant_id)',
        "Ese colorante ya está en la fórmula. Ajuste la dosis existente en "
        "lugar de agregar otra línea.",
    )

    @api.depends('points')
    def _compute_points_display(self):
        for line in self:
            line.points_display = format_points(line.points)

    @api.depends('colorant_id', 'points')
    def _compute_display_name(self):
        for line in self:
            line.display_name = "%s: %s" % (
                line.colorant_id.name or "",
                format_points(line.points),
            )

    @api.constrains('colorant_id')
    def _check_colorant_role(self):
        for line in self:
            if line.colorant_id.tint_role != 'colorant':
                raise ValidationError(_(
                    "«%s» no está marcado como colorante, así que no puede "
                    "dosificarse en una fórmula.",
                    line.colorant_id.display_name,
                ))
