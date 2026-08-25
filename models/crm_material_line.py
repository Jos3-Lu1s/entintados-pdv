from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

class CrmMaterialLine(models.Model):
    _name = 'crm.material.line'
    _description = 'Material de Oportunidad'
    _order = 'id'
    
    lead_id = fields.Many2one(
        "crm.lead",
        string="Oportunidad",
        required=True,
        ondelete="cascade",
        index=True,
    )

    product_id = fields.Many2one(
        "product.product",
        string="Producto",
        required=True,
    )
    
    code_product = fields.Char(
        string="Código de producto",
        related="product_id.default_code",
        readonly=True,
    )
    
    description = fields.Text(
        string="Descripción",
    )

    quantity = fields.Float(
        string="Cantidad solicitada",
        required=True,
        default=1.0,
    )

    uom_id = fields.Many2one(
        "uom.uom",
        string="Unidad de medida",
    )

    standard_price = fields.Float(
        string="Costo unitario",
        related="product_id.standard_price",
        readonly=True,
    )

    subtotal = fields.Float(
        string="Costo",
        compute="_compute_subtotal",
        store=True,
    )

    stock_move_id = fields.Many2one(
        "stock.move",
        string="Movimiento generado",
        readonly=True,
        copy=False,
    )

    @api.depends("product_id", "quantity")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = (
                line.quantity * line.standard_price
            )
            
    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self:
            if line.product_id:
                line.uom_id = line.product_id.uom_id
            else:
                line.uom_id = False
                
    def write(self, vals):
        for line in self:
            if line.lead_id.picking_count > 0:
                raise ValidationError(_(
                    "No se pueden modificar las líneas de material una vez "
                    "generada la salida de inventario."
                ))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            lead_id = vals.get('lead_id')
            if lead_id:
                lead = self.env['crm.lead'].browse(lead_id)
                if lead.picking_count > 0:
                    raise ValidationError(_(
                        "No se pueden agregar líneas de material una vez "
                        "generada la salida de inventario."
                    ))
        return super().create(vals_list)
            