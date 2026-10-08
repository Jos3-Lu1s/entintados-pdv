from odoo import api, fields, models


class ProductPricelistItem(models.Model):
    _inherit = 'product.pricelist.item'

    display_applied_on = fields.Selection(
        selection_add=[('3_esquema', 'Esquema')],
        ondelete={'3_esquema': 'set default'},
    )

    esquema_id = fields.Many2one(
        'tint.schema',
        string='Esquema',
    )

    linea_id = fields.Many2one(
        'lines.product',
        string='Línea',
    )

    @api.onchange('display_applied_on')
    def _onchange_applied_on_esquema(self):
        if self.display_applied_on != '3_esquema':
            self.esquema_id = False
            self.linea_id = False

    @api.onchange('display_applied_on', 'esquema_id', 'linea_id')
    def _compute_name(self):
        super()._compute_name()
        for record in self:
            if record.display_applied_on != '3_esquema':
                continue
            if not record.esquema_id:
                record.name = "Sin Esquema"
            elif not record.linea_id:
                record.name = f"Esquema: {record.esquema_id.name}"
            else:
                record.name = f"Esquema: {record.esquema_id.name} - Linea: {record.linea_id.name}"

    def _is_applicable_for(self, product, qty_in_product_uom):
        self.ensure_one()
        product.ensure_one()

        if self.display_applied_on != '3_esquema':
            return super()._is_applicable_for(product, qty_in_product_uom)

        if self.min_quantity and qty_in_product_uom < self.min_quantity:
            return False
        if not product.scheme_id or product.scheme_id != self.esquema_id:
            return False
        if self.linea_id and product.lines_product_id != self.linea_id:
            return False
        return True
