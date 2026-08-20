from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class LinesProduct(models.Model):
    _name = 'lines.product'
    _description = 'Línea de producto'

    name = fields.Char(string='Nombre', required=True)
    presentation_line_ids = fields.One2many(
        comodel_name='lines.product.presentation',
        inverse_name='line_id',
        string='Presentaciones',
    )
    scheme = fields.Many2one(
        comodel_name='tint.schema',
        string='Esquema',
        required=True,
    )
    
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

class LinesProductPresentation(models.Model):
    _name = 'lines.product.presentation'
    _description = 'Presentación de línea de producto'
    _order = 'line_id, presentation_id, id'

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
            if presentation.price_min >= presentation.price_max:
                raise ValidationError(_(
                    'El precio mínimo (%(min)s) debe ser menor '
                    'que el precio máximo (%(max)s).',
                    min=presentation.price_min,
                    max=presentation.price_max,
                ))