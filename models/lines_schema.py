from odoo import models, fields, api

class LinesSchema(models.Model):
    _name = 'lines.schema'
    _description = 'Product Lines Schema'
    _order = 'name'
    
    name = fields.Char(
        string='Línea',
        required=True,
    )
    
    schema_id = fields.Many2one(
        'product.schema',
        string='Esquema',
        required=True,
    )