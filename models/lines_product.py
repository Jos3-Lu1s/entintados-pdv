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
    
    