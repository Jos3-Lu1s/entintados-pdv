# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..utils.points import format_points


class TintBaseType(models.Model):

    _name = 'tint.base.type'
    _description = "Tipo de base de pintura"
    _order = 'sequence, code, id'
    _inherit = ['pos.load.mixin', 'tint.code.mixin']

    name = fields.Char(
        string="Tipo de base", required=True, translate=True,
        help="Nombre de la base de pintura sobre la que se entinta.")
    code = fields.Char(
        help="Código de una letra del fabricante: W, M, D, A, N, Y, R.")
    description = fields.Html(
        string="Descripción", translate=True, sanitize=True,
        help="Nota interna sobre las características o el uso de esta base.")
    sequence = fields.Integer(
        string="Secuencia", default=10,
        help="Orden en que se muestra el tipo de base en listados y en caja.")
    active = fields.Boolean(
        string="Activo", default=True,
        help="Si se desmarca, el tipo de base se archiva y deja de ofrecerse.")

    white_content = fields.Selection(
        selection=[
            ('high', "Alto"),
            ('medium', "Mediano"),
            ('low', "Bajo"),
            ('very_low', "Muy bajo"),
            ('none', "Sin blanco (transparente)"),
        ],
        string="Contenido de blanco",
        help="A menor contenido de blanco, mayor cantidad de colorante admite la base.")
    fill_percentage = fields.Float(
        string="Llenado de envase (%)", required=True, default=100.0, digits=(5, 2),
        help="Porcentaje del envase que viene lleno de fábrica. Las bases "
             "envasadas al 100% requieren extracción previa antes de entintar.")
    points_per_liter = fields.Integer(
        string="Puntos por litro", required=True,
        help="Puntos de colorante que admite un litro de esta base. Se usa "
             "para verificar la consistencia de la matriz de capacidad.")

    is_line_color = fields.Boolean(
        string="Es color de línea",
        help="La base se vende también sin entintar como color terminado.")
    line_color_codes = fields.Char(
        string="Códigos de línea",
        help="Códigos comerciales del color de línea, p. ej. 1510, 1710, 9115.")

    requires_extraction = fields.Boolean(
        string="Requiere extracción previa",
        help="Si está marcado, hay que extraer parte del contenido del envase "
             "antes de dispensar colorante para evitar el sobrellenado.")
    extraction_percentage = fields.Float(
        string="Extracción previa (%)", digits=(5, 2),
        help="Porcentaje del contenido que debe extraerse antes de entintar.")
    operator_note = fields.Text(
        string="Instrucción al operador", translate=True,
        help="Texto que se muestra en caja al configurar el entintado y que "
             "se imprime en la etiqueta del envase.")

    capacity_ids = fields.One2many(
        comodel_name='tint.base.capacity', inverse_name='base_type_id',
        string="Capacidades por presentación",
        help="Colorante máximo que admite esta base en cada presentación.")
    capacity_count = fields.Integer(
        string="Capacidades definidas", compute='_compute_capacity_count',
        help="Número de presentaciones con capacidad registrada para esta base.")

    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        "Ya existe un tipo de base con ese código.",
    )
    _points_per_liter_positive = models.Constraint(
        'CHECK (points_per_liter > 0)',
        "Los puntos por litro deben ser mayores que cero.",
    )
    _fill_percentage_range = models.Constraint(
        'CHECK (fill_percentage > 0 AND fill_percentage <= 100)',
        "El llenado del envase debe estar entre 0 y 100 por ciento.",
    )
    _extraction_percentage_range = models.Constraint(
        'CHECK (extraction_percentage >= 0 AND extraction_percentage < 100)',
        "La extracción previa debe estar entre 0 y 100 por ciento.",
    )

    @api.depends('capacity_ids')
    def _compute_capacity_count(self):
        data = self.env['tint.base.capacity']._read_group(
            domain=[('base_type_id', 'in', self.ids)],
            groupby=['base_type_id'],
            aggregates=['__count'],
        )
        counts = {base_type.id: count for base_type, count in data}
        for base_type in self:
            base_type.capacity_count = counts.get(base_type.id, 0)

    @api.depends('name', 'code')
    def _compute_display_name(self):
        for base_type in self:
            base_type.display_name = (
                "%s — %s" % (base_type.code, base_type.name)
                if base_type.code else base_type.name
            )

    @api.constrains('requires_extraction', 'extraction_percentage')
    def _check_extraction(self):
        for base_type in self:
            if base_type.requires_extraction and base_type.extraction_percentage <= 0:
                raise ValidationError(_(
                    "El tipo de base «%s» requiere extracción previa, así que el "
                    "porcentaje de extracción debe ser mayor que cero.",
                    base_type.display_name,
                ))

    def capacity_for(self, size, raise_if_missing=True):
        self.ensure_one()
        capacity = self.capacity_ids.filtered(lambda c: c.size_id == size)[:1]
        if not capacity:
            if raise_if_missing:
                raise UserError(_(
                    "No hay capacidad de colorante definida para la base «%(base)s» "
                    "en la presentación «%(size)s». Captúrela en la matriz de "
                    "capacidad antes de usar esta combinación.",
                    base=self.display_name,
                    size=size.display_name,
                ))
            return 0
        return capacity.max_points

    def capacity_display_for(self, size, raise_if_missing=True):
        """Capacidad en notación mixta, p. ej. ``9Y 24``."""
        return format_points(self.capacity_for(size, raise_if_missing=raise_if_missing))

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            'id', 'name', 'code', 'fill_percentage', 'points_per_liter',
            'requires_extraction', 'extraction_percentage', 'operator_note',
            # El panel ordena los tipos de base por secuencia.
            'sequence',
        ]

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('active', '=', True)]
