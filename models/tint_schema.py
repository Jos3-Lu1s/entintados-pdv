# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class TintSchema(models.Model):
    _name = 'tint.schema'
    _description = "Esquemas de las bases"
    _inherit = ['pos.load.mixin']

    active = fields.Boolean(
        string="Activo", default=True,
        help="Si está desactivado, el esquema no aparecerá en búsquedas ni en el punto de venta.")
    name = fields.Char(
        string="Nombre", required=True, translate=True,
        help="Nombre del esquema para la base.")

    line_ids = fields.One2many(
        comodel_name='lines.product',
        inverse_name='scheme',
        string='Líneas de producto',
    )
    product_ids = fields.One2many(
        comodel_name='product.template',
        inverse_name='scheme_id',
        string='Productos',
    )

    line_count = fields.Integer(
        string='Líneas',
        compute='_compute_counts',
    )
    product_count = fields.Integer(
        string='Productos',
        compute='_compute_counts',
    )
    notes = fields.Html(
        string='Notas',
        help="Información adicional o consideraciones técnicas sobre este esquema.")

    @api.depends('line_ids', 'product_ids')
    def _compute_counts(self):
        for schema in self:
            schema.line_count = len(schema.line_ids)
            schema.product_count = len(schema.product_ids)

    def action_view_lines(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('entintados_pdv.lines_product_action')
        action['domain'] = [('scheme', '=', self.id)]
        action['context'] = {
            'default_scheme': self.id,
            'search_default_scheme': self.id,
        }
        return action

    def action_view_products(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('entintados_pdv.product_template_action_tint')
        action['domain'] = [('scheme_id', '=', self.id)]
        action['context'] = {
            'search_default_tint_base': 1,
            'default_scheme_id': self.id,
        }
        return action

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name']

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('active', '=', True)]