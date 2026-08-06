from odoo import models, fields, api

class Schema(models.Model):
    _name = 'product.schema'
    _description = 'Product Schema'
    _order = 'name'
    
    name = fields.Char(
        string='Esquema',
        required=True,
    )
    
    product_discount = fields.Float(
        string='Descuento del esquema',
        default=0.0,
    )