from odoo import api, fields, models


class Schema(models.Model):
    _name = 'product.schema'
    _description = 'Product Schema'
    _order = 'name'
    _inherit = ['pos.load.mixin']

    name = fields.Char(
        string='Esquema',
        required=True,
    )

    product_discount = fields.Float(
        string='Descuento del esquema',
        default=0.0,
    )

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name', 'product_discount']

    @api.model
    def _load_pos_data_domain(self, data, config):
        # Sin campo `active`: los esquemas son pocos y se cargan todos.
        return []
