# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TintGallery(models.Model):
    """Familia de fórmulas según su origen.

    Una galería agrupa las fórmulas que vienen de una misma fuente: el
    catálogo propio, el de un fabricante de la competencia, los colores
    descontinuados que aún se reproducen, o los desarrollos internos.
    La galería agrupa **fórmulas**: un mismo color puede resolverse con
    dosis distintas según de quién sea la receta.
    """

    _name = 'tint.gallery'
    _description = "Galería de fórmulas"
    _order = 'sequence, name, id'
    _inherit = ['pos.load.mixin', 'tint.code.mixin']

    name = fields.Char(
        string="Galería", required=True, translate=True,
        help="Nombre de la galería, p. ej. Milenium, Comex o Verel.")
    code = fields.Char(
        index='btree_not_null',
        help="Código corto para identificarla en listados y etiquetas.")
    sequence = fields.Integer(
        string="Secuencia", default=10,
        help="Orden en que se muestra la galería en listados y en caja.")
    active = fields.Boolean(
        string="Activa", default=True,
        help="Si se desmarca, la galería se archiva y deja de ofrecerse.")

    code_prefix = fields.Char(
        string="Prefijo de código",
        help="Inicio del código de los colores de esta galería, p. ej. R o ODM-. "
             "Puede quedar vacío. Cambiarlo solo afecta a los colores que se "
             "creen después.")
    color_sequence_id = fields.Many2one(
        comodel_name='ir.sequence', string="Secuencia de colores",
        readonly=True, copy=False, ondelete='restrict',
        help="Numeración automática de los colores de esta galería.")
    color_number_next = fields.Integer(
        string="Siguiente número",
        related='color_sequence_id.number_next_actual', readonly=False,
        help="Número que se intentará asignar al siguiente color sin número. "
             "Si su código ya está ocupado, se salta al siguiente libre.")

    description = fields.Html(
        string="Descripción", translate=True, sanitize=True,
        help="Nota interna sobre el origen o el uso de esta galería.")

    formula_ids = fields.One2many(
        comodel_name='tint.color.formula', inverse_name='gallery_id',
        string="Fórmulas",
        help="Fórmulas de entintado que pertenecen a esta galería.")
    formula_count = fields.Integer(
        string="Número de fórmulas", compute='_compute_counts',
        help="Número total de fórmulas registradas en esta galería.")
    color_count = fields.Integer(
        string="Colores", compute='_compute_counts',
        help="Colores que pertenecen a esta galería.")

    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        "Ya existe una galería con ese código.",
    )

    @staticmethod
    def _normalize_prefix(prefix):
        return (prefix or '').strip().upper() or False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'code_prefix' in vals:
                vals['code_prefix'] = self._normalize_prefix(vals['code_prefix'])
        galleries = super().create(vals_list)
        for gallery in galleries:
            gallery.color_sequence_id = self.env['ir.sequence'].sudo().create({
                'name': _("Colores: %s", gallery.name),
                'padding': 4,
                'implementation': 'standard',
                'number_next': 1,
                'company_id': False,
            })
        return galleries

    def write(self, vals):
        if 'code_prefix' in vals:
            vals['code_prefix'] = self._normalize_prefix(vals['code_prefix'])
        return super().write(vals)

    # --- Código de los colores --------------------------------------------

    def _compose_color_code(self, number):
        self.ensure_one()
        return (self.code_prefix or '') + number.zfill(4)

    def _next_color_number(self):
        """Siguiente número de la secuencia cuyo código no esté ocupado."""
        self.ensure_one()
        if not self.color_sequence_id:
            raise UserError(_(
                "La galería «%s» no tiene numeración de colores configurada. "
                "Capture el número del color o configure la galería.",
                self.name,
            ))
        colors = self.env['tint.color'].sudo().with_context(active_test=False)
        sequence = self.color_sequence_id.sudo()
        while True:
            number = sequence.next_by_id()
            code = self._compose_color_code(number)
            if not colors.search_count([('code', '=', code)], limit=1):
                return number

    @api.depends('formula_ids')
    def _compute_counts(self):
        formulas = dict(self.env['tint.color.formula']._read_group(
            domain=[('gallery_id', 'in', self.ids)],
            groupby=['gallery_id'],
            aggregates=['__count'],
        ))
        colors = dict(self.env['tint.color']._read_group(
            domain=[('gallery_id', 'in', self.ids)],
            groupby=['gallery_id'],
            aggregates=['__count'],
        ))
        for gallery in self:
            gallery.formula_count = formulas.get(gallery, 0)
            gallery.color_count = colors.get(gallery, 0)

    @api.depends('name', 'code')
    def _compute_display_name(self):
        for gallery in self:
            gallery.display_name = (
                "[%s] %s" % (gallery.code, gallery.name)
                if gallery.code else gallery.name
            )

    # --- Acciones -------------------------------------------------------

    def action_open_formulas(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Fórmulas: %s", self.display_name),
            'res_model': 'tint.color.formula',
            'view_mode': 'list,form',
            'domain': [('gallery_id', '=', self.id)],
        }

    def action_open_colors(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Colores: %s", self.display_name),
            'res_model': 'tint.color',
            'view_mode': 'list,form',
            'domain': [('gallery_id', '=', self.id)],
            'context': self.env.context,
        }

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name', 'code', 'code_prefix', 'sequence']

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('active', '=', True)]
