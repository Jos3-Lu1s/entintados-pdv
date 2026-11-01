# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..utils.usage import count_usage, format_usage


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
        string='Número de productos',
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

    def unlink(self):
        self._check_not_in_use()
        return super().unlink()

    def _check_not_in_use(self):
        # Productos y fórmulas llegan al esquema por su línea: los cubre el conteo de líneas.
        usage = count_usage(self.env, self, [
            ('lines.product', 'scheme', self.env._("línea de producto"), self.env._("líneas de producto")),
            ('res.partner.discount.rule', 'scheme_id', self.env._("acuerdo de cliente"), self.env._("acuerdos de cliente")),
            ('product.pricelist.item', 'esquema_id', self.env._("regla de tarifa"), self.env._("reglas de tarifa")),
        ])
        if usage:
            raise UserError("\n".join(
                self.env._("No se puede eliminar el esquema «%(name)s»: lo usan %(usage)s. "
                           "Archívalo en su lugar.",
                           name=schema.display_name, usage=format_usage(self.env, usage[schema.id]))
                for schema in self if schema.id in usage
            ))

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