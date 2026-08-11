# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..utils.points import format_points


class TintColorFormulaLine(models.Model):
    _name = 'tint.color.formula.line'
    _description = "Dosis de colorante de una fórmula"
    _order = 'formula_id, sequence, id'
    _inherit = ['pos.load.mixin']

    formula_id = fields.Many2one(
        comodel_name='tint.color.formula', string="Fórmula",
        required=True, ondelete='cascade', index=True,
        help="Fórmula de entintado a la que pertenece esta dosis.")
    colorant_id = fields.Many2one(
        comodel_name='product.product', string="Colorante",
        required=True, ondelete='restrict', index=True,
        domain="[('tint_role', '=', 'colorant')]",
        help="Producto colorante que se dispensa en esta línea.")
    points = fields.Integer(
        string="Dosis (Pts.)", required=True, default=1,
        help="Puntos de colorante a dispensar. Una onza equivale a 48 puntos.")
    points_display = fields.Char(
        string="Dosis", compute='_compute_points_display',
        help="La dosis en la notación mixta de la operación, p. ej. 9Y 24.")
    sequence = fields.Integer(
        string="Secuencia", default=10,
        help="Orden en que se dispensan los colorantes de la fórmula.")

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

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'formula_id', 'colorant_id', 'points', 'sequence']

    @api.model
    def _load_pos_data_domain(self, data, config):
        # Sin campo `active`: las dosis se cargan junto con sus fórmulas.
        return []
