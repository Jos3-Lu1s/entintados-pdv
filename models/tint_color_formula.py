# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..utils.points import format_points


class TintColorFormula(models.Model):
    _name = 'tint.color.formula'
    _description = "Fórmula de entintado"
    _order = 'gallery_id, color_id, base_type_id, size_id, id'
    _inherit = ['pos.load.mixin']

    gallery_id = fields.Many2one(
        comodel_name='tint.gallery', string="Galería",
        required=True, ondelete='restrict', index=True,
        help="Origen de la receta: catálogo propio, de un fabricante de la "
             "competencia, histórico o desarrollo interno.")
    color_id = fields.Many2one(
        comodel_name='tint.color', string="Color",
        required=True, ondelete='cascade', index=True)
    base_type_id = fields.Many2one(
        comodel_name='tint.base.type', string="Tipo de base",
        required=True, ondelete='restrict', index=True)
    size_id = fields.Many2one(
        comodel_name='tint.size', string="Presentación",
        required=True, ondelete='restrict', index=True)
    active = fields.Boolean(string="Activo", default=True)

    line_ids = fields.One2many(
        comodel_name='tint.color.formula.line', inverse_name='formula_id',
        string="Dosis de colorante")
    
    scheme_id = fields.Many2one(
        related='color_id.scheme_id', string="Esquema de producto",
        store=True, readonly=True,)

    total_points = fields.Integer(
        string="Total (Pts.)", compute='_compute_total_points', store=True)
    total_points_display = fields.Char(
        string="Total", compute='_compute_total_points', store=True)
    capacity_points = fields.Integer(
        string="Capacidad del envase (Pts.)", compute='_compute_capacity')
    capacity_display = fields.Char(
        string="Capacidad del envase", compute='_compute_capacity')
    remaining_points = fields.Integer(
        string="Holgura (Pts.)", compute='_compute_capacity',
        help="Puntos que aún admite el envase con esta fórmula.")
    fits = fields.Boolean(
        string="Cabe en el envase", compute='_compute_capacity',
        search='_search_fits')

    requires_extraction = fields.Boolean(
        related='base_type_id.requires_extraction', readonly=True)
    operator_note = fields.Text(
        related='base_type_id.operator_note', readonly=True)

    # La galería forma parte de la llave a propósito: el sentido de tener
    # galerías es que dos fabricantes puedan dar recetas distintas para el
    # mismo color sobre la misma base y presentación. Sin ella en la llave,
    # registrar la equivalencia de un color de la competencia sería imposible.
    _gallery_color_base_size_uniq = models.Constraint(
        'UNIQUE(gallery_id, color_id, base_type_id, size_id)',
        "Esa galería ya tiene una fórmula para ese color sobre esa base y "
        "presentación.",
    )

    # --- Cálculos -------------------------------------------------------

    @api.depends('line_ids.points')
    def _compute_total_points(self):
        for formula in self:
            total = sum(formula.line_ids.mapped('points'))
            formula.total_points = total
            formula.total_points_display = format_points(total)

    @api.depends('base_type_id', 'size_id', 'total_points')
    def _compute_capacity(self):
        """Tolerante a propósito: no interrumpe la captura.

        La exigencia de que la fórmula quepa vive en `_check_fits`, que
        actúa al guardar.
        """
        for formula in self:
            capacity = 0
            if formula.base_type_id and formula.size_id:
                capacity = formula.base_type_id.capacity_for(
                    formula.size_id, raise_if_missing=False)
            formula.capacity_points = capacity
            formula.capacity_display = format_points(capacity) if capacity else ""
            formula.remaining_points = capacity - formula.total_points
            formula.fits = bool(capacity) and formula.total_points <= capacity

    def _search_fits(self, operator, value):
        """Permite filtrar por «cabe en el envase» pese a ser campo calculado.

        `fits` no se almacena a propósito, para que siempre refleje la matriz
        de capacidad vigente. Sin este método, cualquier filtro sobre él
        fallaría al validar la vista. La comparación se resuelve contra la
        matriz completa, que son pocas decenas de registros.
        """
        if operator not in ('=', '!=') or not isinstance(value, bool):
            raise UserError(_(
                "El filtro «Cabe en el envase» solo admite comparación por "
                "igualdad con verdadero o falso."
            ))
        capacities = {
            (capacity.base_type_id.id, capacity.size_id.id): capacity.max_points
            for capacity in self.env['tint.base.capacity'].search([])
        }
        todas = self.with_context(active_test=False).search([])
        caben = todas.filtered(
            lambda f: capacities.get((f.base_type_id.id, f.size_id.id), 0)
            and f.total_points <= capacities[(f.base_type_id.id, f.size_id.id)]
        )
        busca_las_que_caben = (operator == '=') == value
        objetivo = caben if busca_las_que_caben else (todas - caben)
        return [('id', 'in', objetivo.ids)]

    @api.depends('gallery_id', 'color_id', 'base_type_id', 'size_id')
    def _compute_display_name(self):
        for formula in self:
            formula.display_name = "%s · %s · %s · %s" % (
                formula.gallery_id.code or formula.gallery_id.name or "",
                formula.color_id.name or "",
                formula.base_type_id.code or "",
                formula.size_id.name or "",
            )

    # --- Validaciones ---------------------------------------------------

    @api.constrains('base_type_id', 'size_id', 'total_points')
    def _check_fits(self):
        """Una fórmula que no cabe en el envase se derrama al dispensar."""
        for formula in self:
            capacity = formula.base_type_id.capacity_for(formula.size_id)
            if formula.total_points > capacity:
                raise ValidationError(_(
                    "La fórmula «%(formula)s» pide %(total)s de colorante, pero "
                    "el envase admite como máximo %(capacity)s. Excede por "
                    "%(excess)s.",
                    formula=formula.display_name,
                    total=format_points(formula.total_points),
                    capacity=format_points(capacity),
                    excess=format_points(formula.total_points - capacity),
                ))

    # --- Acciones -------------------------------------------------------

    def action_generate_other_sizes(self):
        """Crea las fórmulas de las demás presentaciones escalando las dosis.

        Evita capturar tres veces el mismo color. El escalado es una
        propuesta, no una verdad: las dosis quedan editables porque la
        fuente de verdad es la carta del fabricante, que puede no escalar
        de forma perfectamente lineal.
        """
        creadas = self.env['tint.color.formula']
        for formula in self:
            if not formula.line_ids:
                raise UserError(_(
                    "La fórmula «%s» no tiene dosis que escalar.",
                    formula.display_name,
                ))
            origen = formula.size_id.volume_liters
            if not origen:
                raise UserError(_(
                    "La presentación «%s» no tiene volumen definido, así que no "
                    "se puede escalar desde ella.",
                    formula.size_id.display_name,
                ))
            otras = self.env['tint.size'].search([('id', '!=', formula.size_id.id)])
            for size in otras:
                # Solo cuenta lo que ya exista en LA MISMA galería: que Comex
                # tenga la fórmula en galón no significa que la nuestra la tenga.
                if formula.color_id.formula_for(
                        formula.base_type_id, size, gallery=formula.gallery_id):
                    continue  # ya existe: no se sobreescribe trabajo capturado
                if not formula.base_type_id.capacity_for(size, raise_if_missing=False):
                    continue  # combinación fuera de la matriz
                factor = size.volume_liters / origen
                lineas = [
                    (0, 0, {
                        'colorant_id': line.colorant_id.id,
                        'points': max(1, round(line.points * factor)),
                        'sequence': line.sequence,
                    })
                    for line in formula.line_ids
                ]
                creadas |= self.create({
                    'gallery_id': formula.gallery_id.id,
                    'color_id': formula.color_id.id,
                    'base_type_id': formula.base_type_id.id,
                    'size_id': size.id,
                    'line_ids': lineas,
                })
        if not creadas:
            raise UserError(_(
                "No había presentaciones pendientes por generar para esta fórmula."
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Fórmulas generadas"),
            'res_model': 'tint.color.formula',
            'view_mode': 'list,form',
            'domain': [('id', 'in', creadas.ids)],
        }

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            'id', 'color_id', 'base_type_id', 'size_id', 'total_points',
            'line_ids',
            # Primer nivel del filtrado escalonado en caja. Sin él, en el
            # cliente toda fórmula parece huérfana de esquema aunque su color
            # lo tenga asignado.
            'scheme_id',
            'gallery_id',
        ]

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('active', '=', True)]
