# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class TintColor(models.Model):
    _name = 'tint.color'
    _description = "Color de la carta"
    _order = 'name, id'
    _inherit = ['pos.load.mixin', 'tint.code.mixin']

    name = fields.Char(
        string="Color", required=True, translate=True,
        help="Nombre comercial del color de la carta.")
    gallery_id = fields.Many2one(
        comodel_name='tint.gallery', string="Galería",
        required=True, ondelete='restrict', index=True,
        help="Galería a la que pertenece el color. Su prefijo inicia el código.")
    code_number = fields.Char(
        string="Número", copy=False,
        help="Solo dígitos, sin el prefijo de la galería. Vacío: se asigna el "
             "siguiente de la galería.")
    gallery_code_prefix = fields.Char(
        string="Prefijo", related='gallery_id.code_prefix')
    code_matches_gallery = fields.Boolean(
        compute='_compute_code_matches_gallery',
        help="El código guardado es el prefijo actual de la galería más el número.")
    code = fields.Char(
        copy=False, index='btree_not_null',
        help="Prefijo de la galería más el número del color. No puede repetirse.")
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
        string="Número de fórmulas", compute='_compute_formula_count',
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

    @api.depends('formula_ids.base_type_id', 'formula_ids.active')
    def _compute_base_type_summary(self):
        for color in self:
            # base_type_ids ya viene ordenado por secuencia del tipo de base.
            active_formulas = color.formula_ids.filtered('active')
            names = active_formulas.base_type_id.mapped('name')
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

    # --- Código ---------------------------------------------------------

    # Con los valores guardados: durante la edición el código aún no se recompone.
    @api.depends('code', 'code_number', 'gallery_id.code_prefix')
    def _compute_code_matches_gallery(self):
        for color in self:
            origin = color._origin
            color.code_matches_gallery = not origin.code or bool(
                origin.code_number and origin.gallery_id
                and origin.code == origin.gallery_id._compose_color_code(origin.code_number)
            )

    @api.onchange('gallery_id')
    def _onchange_gallery_id_code_number(self):
        # Al guardar con otra galería se asigna el siguiente de esa galería.
        origin = self._origin
        if origin.id:
            self.code_number = (
                origin.code_number if self.gallery_id == origin.gallery_id else False
            )

    @staticmethod
    def _clean_code_number(number):
        number = str(number).strip()
        if not (number.isascii() and number.isdigit()):
            raise ValidationError(_(
                "El número del color solo admite dígitos, sin el prefijo de la "
                "galería: «%s».", number,
            ))
        return number.zfill(4)

    def _code_vals(self, gallery, vals):
        """Código y número del color según lo que traen los valores.

        Un código explícito se respeta; si no, manda el número capturado y,
        sin número, la secuencia de la galería.
        """
        prefix = gallery.code_prefix or ''
        if vals.get('code'):
            code = self._normalize_code(vals['code'])
            number = code[len(prefix):] if code.startswith(prefix) else ''
            return {
                'code': code,
                'code_number': number if number.isascii() and number.isdigit() else False,
            }
        if vals.get('code_number'):
            number = self._clean_code_number(vals['code_number'])
            code = gallery._compose_color_code(number)
            taken = self.sudo().with_context(active_test=False).search(
                [('code', '=', code), ('id', 'not in', self.ids)], limit=1)
            if taken:
                raise ValidationError(_(
                    "El código «%(code)s» ya lo tiene el color «%(color)s».",
                    code=code, color=taken.name,
                ))
        else:
            number = gallery._next_color_number()
        return {'code': gallery._compose_color_code(number), 'code_number': number}

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            gallery = self.env['tint.gallery'].browse(vals.get('gallery_id'))
            if not gallery:
                raise ValidationError(_(
                    "El color «%s» necesita una galería.", vals.get('name') or "",
                ))
            vals.update(self._code_vals(gallery, vals))
        return super().create(vals_list)

    def write(self, vals):
        if not {'code', 'code_number', 'gallery_id'} & vals.keys():
            return super().write(vals)
        # El código depende de la galería y el número de cada color.
        for color in self:
            gallery = (
                self.env['tint.gallery'].browse(vals['gallery_id'])
                if 'gallery_id' in vals else color.gallery_id
            )
            if not gallery:
                raise ValidationError(_(
                    "El color «%s» necesita una galería.", color.name,
                ))
            color_vals = dict(vals)
            if 'code' in vals:
                color_vals.update(color._code_vals(gallery, {'code': vals['code']}))
            elif 'code_number' in vals:
                color_vals.update(color._code_vals(
                    gallery, {'code_number': vals['code_number']}))
            elif gallery != color.gallery_id:
                color_vals.update(color._code_vals(gallery, {}))
            super(TintColor, color).write(color_vals)
        return True
