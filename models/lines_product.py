from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class LinesProduct(models.Model):
    _name = 'lines.product'
    _description = 'Línea de producto'
    _inherit = ['pos.load.mixin']

    active = fields.Boolean(
        string='Activo', default=True,
        help="Si está desactivado, la línea no aparecerá en búsquedas ni en el punto de venta.")
    name = fields.Char(string='Nombre', required=True)
    scheme = fields.Many2one(
        comodel_name='tint.schema',
        string='Esquema',
        required=True,
    )
    presentation_line_ids = fields.One2many(
        comodel_name='lines.product.presentation',
        inverse_name='line_id',
        string='Presentaciones',
    )
    product_ids = fields.One2many(
        comodel_name='product.template',
        inverse_name='lines_product_id',
        string='Productos',
    )

    presentation_count = fields.Integer(
        string='Presentaciones',
        compute='_compute_counts',
    )
    product_count = fields.Integer(
        string='Productos',
        compute='_compute_counts',
    )
    notes = fields.Html(
        string='Notas',
        help="Información adicional o notas sobre esta línea de producto.")
    
    @api.depends('presentation_line_ids', 'product_ids')
    def _compute_counts(self):
        for line in self:
            line.presentation_count = len(line.presentation_line_ids)
            line.product_count = len(line.product_ids)

    def action_view_products(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('entintados_pdv.product_template_action_tint')
        action['domain'] = [('lines_product_id', '=', self.id)]
        action['context'] = {
            'search_default_tint_base': 1,
            'default_lines_product_id': self.id,
        }
        return action
    
    @api.depends('name', 'scheme', 'scheme.name')
    def _compute_display_name(self):
        for line in self:
            if line.scheme:
                line.display_name = f'{line.name} - {line.scheme.name}'
            else:
                line.display_name = line.name
    
    @api.constrains('name', 'scheme')
    def _check_unique_name_scheme(self):
        for line in self:
            if not line.name or not line.scheme:
                continue

            normalized_name = line.name.strip()

            duplicate = self.search([
                ('id', '!=', line.id),
                ('scheme', '=', line.scheme.id),
                ('name', '=ilike', normalized_name),
            ], limit=1)

            if duplicate:
                raise ValidationError(_(
                    'Ya existe la línea "%(name)s" para el esquema "%(scheme)s".',
                    name=normalized_name,
                    scheme=line.scheme.display_name,
                ))

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name', 'scheme']

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('active', '=', True)]


class LinesProductPresentation(models.Model):
    _name = 'lines.product.presentation'
    _description = 'Presentación de línea de producto'
    _order = 'line_id, presentation_id, id'
    _inherit = ['pos.load.mixin']

    line_id = fields.Many2one(
        comodel_name='lines.product', string='Línea',
        required=True, ondelete='cascade', index=True,
    )
    presentation_id = fields.Many2one(
        comodel_name='tint.size', string='Presentación',
        required=True, ondelete='restrict', index=True,
    )
    price_osel = fields.Float(string='Precio osel', compute='_compute_price_osel', store=True)
    price_min = fields.Float(string='Precio mínimo')
    price_max = fields.Float(string='Precio máximo')

    _presentation_uniq = models.Constraint(
        'UNIQUE(line_id, presentation_id)',
        'Esa presentación ya está agregada a esta línea.',
    )
    
    @api.depends('price_min')
    def _compute_price_osel(self):
        for presentation in self:
            presentation.price_osel = presentation.price_min
    
    @api.constrains('price_min', 'price_max')
    def _check_price_range(self):
        for presentation in self:
            if presentation.price_min < 0 or presentation.price_max < 0:
                raise ValidationError(_(
                    'Los precios mínimo y máximo no pueden ser valores negativos.'
                ))
            if presentation.price_max > 0 and presentation.price_min >= presentation.price_max:
                raise ValidationError(_(
                    'El precio mínimo (%(min)s) debe ser estrictamente menor '
                    'que el precio máximo (%(max)s).',
                    min=presentation.price_min,
                    max=presentation.price_max,
                ))

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            'id',
            'line_id',
            'presentation_id',
            'price_osel',
            'price_min',
            'price_max',
        ]

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('line_id.active', '=', True)]