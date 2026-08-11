# -*- coding: utf-8 -*-

from odoo import api, fields, models


class TintGallery(models.Model):
    """Familia de fórmulas según su origen.

    Una galería agrupa las fórmulas que vienen de una misma fuente: el
    catálogo propio, el de un fabricante de la competencia, los colores
    descontinuados que aún se reproducen, o los desarrollos internos.

    No confundir con `tint.collection`, que agrupa **colores** en cartas
    comerciales. La galería agrupa **fórmulas**: un mismo color puede
    resolverse con dosis distintas según de quién sea la receta.
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

    description = fields.Html(
        string="Descripción", translate=True, sanitize=True,
        help="Nota interna sobre el origen o el uso de esta galería.")

    formula_ids = fields.One2many(
        comodel_name='tint.color.formula', inverse_name='gallery_id',
        string="Fórmulas",
        help="Fórmulas de entintado que pertenecen a esta galería.")
    formula_count = fields.Integer(
        string="Fórmulas", compute='_compute_counts',
        help="Número total de fórmulas registradas en esta galería.")
    color_count = fields.Integer(
        string="Colores", compute='_compute_counts',
        help="Colores distintos con al menos una fórmula en esta galería.")

    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        "Ya existe una galería con ese código.",
    )

    @api.depends('formula_ids', 'formula_ids.color_id')
    def _compute_counts(self):
        data = self.env['tint.color.formula']._read_group(
            domain=[('gallery_id', 'in', self.ids)],
            groupby=['gallery_id'],
            aggregates=['__count', 'color_id:count_distinct'],
        )
        counts = {
            gallery.id: (total, colors)
            for gallery, total, colors in data
        }
        for gallery in self:
            total, colors = counts.get(gallery.id, (0, 0))
            gallery.formula_count = total
            gallery.color_count = colors

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
            'name': self.display_name,
            'res_model': 'tint.color.formula',
            'view_mode': 'list,form',
            'domain': [('gallery_id', '=', self.id)],
            'context': {'default_gallery_id': self.id},
        }

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name', 'code', 'sequence']

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('active', '=', True)]
