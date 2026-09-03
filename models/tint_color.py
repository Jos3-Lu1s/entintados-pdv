# -*- coding: utf-8 -*-

from odoo import api, fields, models


class TintColor(models.Model):
    _name = 'tint.color'
    _description = "Color de la carta"
    _order = 'name, id'
    _inherit = ['pos.load.mixin', 'tint.code.mixin']

    name = fields.Char(
        string="Color", required=True, translate=True,
        help="Nombre comercial del color de la carta.")
    code = fields.Char(
        copy=False, index='btree_not_null',
        help="Código del color. Debe capturarse y no puede repetirse.")
    notes = fields.Html(
        string="Notas", translate=True, sanitize=True,
        help="Observaciones internas sobre el color.")
    active = fields.Boolean(
        string="Activo", default=True,
        help="Si se desmarca, el color se archiva y deja de ofrecerse.")

    formula_ids = fields.One2many(
        comodel_name='tint.color.formula', inverse_name='color_id',
        string="Fórmulas",
        help="Fórmulas de entintado registradas para este color.")
    formula_count = fields.Integer(
        string="Fórmulas", compute='_compute_formula_count',
        help="Número de fórmulas registradas para este color.")
    has_formula = fields.Boolean(
        string="Tiene fórmula", compute='_compute_has_formula', store=True,
        help="Indica si el color tiene al menos una fórmula registrada.")
    base_type_ids = fields.Many2many(
        comodel_name='tint.base.type', string="Bases compatibles",
        compute='_compute_base_type_ids', search='_search_base_type_ids',
        help="Tipos de base sobre los que este color tiene fórmula registrada.")
    base_type_summary = fields.Char(
        string="Bases", compute='_compute_base_type_summary', store=True,
        help="Nombres de los tipos de base con fórmula para este color. Se carga "
             "a la caja para mostrarlos junto al color sin abrir sus fórmulas.")

    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        "Ya existe un color con ese código.",
    )

    @api.depends('formula_ids')
    def _compute_formula_count(self):
        data = self.env['tint.color.formula']._read_group(
            domain=[('color_id', 'in', self.ids)],
            groupby=['color_id'],
            aggregates=['__count'],
        )
        counts = {color.id: count for color, count in data}
        for color in self:
            color.formula_count = counts.get(color.id, 0)

    @api.depends('formula_ids')
    def _compute_has_formula(self):
        for color in self:
            color.has_formula = bool(color.formula_ids)

    @api.depends('formula_ids.base_type_id')
    def _compute_base_type_ids(self):
        for color in self:
            color.base_type_ids = color.formula_ids.base_type_id

    def _search_base_type_ids(self, operator, value):
        return [('formula_ids.base_type_id', operator, value)]

    @api.depends('formula_ids.base_type_id')
    def _compute_base_type_summary(self):
        for color in self:
            # base_type_ids ya viene ordenado por secuencia del tipo de base.
            names = color.formula_ids.base_type_id.mapped('name')
            color.base_type_summary = " · ".join(dict.fromkeys(names))

    @api.depends('name', 'code')
    def _compute_display_name(self):
        for color in self:
            color.display_name = (
                "[%s] %s" % (color.code, color.name) if color.code else color.name
            )

    def formula_for(self, base_type, size, gallery=None):
        """Fórmula de este color para esa base y presentación.

        Desde que existen las galerías, la combinación puede repetirse entre
        recetas de distinto origen. Sin acotar la galería se devuelve la
        primera que aparezca, que no tiene por qué ser la deseada.
        """
        self.ensure_one()
        return self.formula_ids.filtered(
            lambda f: f.base_type_id == base_type
            and f.size_id == size
            and (not gallery or f.gallery_id == gallery)
        )[:1]

    def formulas_for_size(self, size, gallery=None):
        self.ensure_one()
        return self.formula_ids.filtered(
            lambda f: f.size_id == size
            and (not gallery or f.gallery_id == gallery)
        )

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        # 'base_type_ids' omitido: el resumen almacenado basta para la caja.
        return ['id', 'name', 'display_name', 'code',
                'has_formula', 'base_type_summary']

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('active', '=', True)]
