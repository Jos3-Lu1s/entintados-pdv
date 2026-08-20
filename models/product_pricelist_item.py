from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..utils.points import format_points

class ProductPricelistItem(models.Model):
    _inherit= 'product.pricelist.item'

    display_applied_on= fields.Selection(
        selection_add=[('3_esquema','Esquema')],
        ondelete={'3_esquema': 'set default'}
    )

    esquema_id = fields.Many2one(
        'tint.schema',
        string='Esquema',
    )

    linea_id=fields.Many2one(
        'lines.product',
        string='Línea',
    )

    @api.onchange('display_applied_on')
    def _onchange_applied_on_esquema(self):
        if self.display_applied_on != '3_esquema':
            self.esquema_id = False
            self.linea_id = False

    @api.onchange('display_applied_on','esquema_id','linea_id')
    def _compute_name(self):
        super()._compute_name()
        final_name=""
        for record in self:
            if record.display_applied_on == '3_esquema':
                if not record.esquema_id:
                    final_name="Sin Esquema"
                elif record.esquema_id and not record.linea_id:
                    final_name=f"Esquema: {record.esquema_id.name}"
                else:
                    final_name=f"Esquema: {record.esquema_id.name} - Linea: {record.linea_id.name}"
                record.name = final_name
    
    def _is_applicable_for(self, product, qty_in_product_uom):
        self.ensure_one()
        product.ensure_one()

        if self.display_applied_on != '3_esquema':
            return super()._is_applicable_for(product, qty_in_product_uom)

        res = True
        if self.min_quantity and qty_in_product_uom < self.min_quantity:
            res = False
        else:
            is_product_template = product._name == 'product.template'
            producto_esquema = product.scheme_id
            producto_linea = product.lines_product_id

            if not producto_esquema or producto_esquema != self.esquema_id:
                res = False
            elif self.linea_id and producto_linea != self.linea_id:
                res = False

        return res