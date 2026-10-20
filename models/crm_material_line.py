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
        domain=[('type', '=', 'consu'), ('is_storable', '=', True)]
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
    
    qty_available_branch = fields.Float(
        string="Disp. sucursal",
        compute="_compute_qty_scopes",
        digits="Product Unit of Measure",
    )
    
    qty_available_company = fields.Float(
        string="Disp. empresa",
        compute="_compute_qty_scopes",
        digits="Product Unit of Measure",
    )
    
    qty_available = fields.Float(
        string="Disponible",
        compute="_compute_qty_available",
        digits="Product Unit of Measure",
    )

    @api.depends("product_id", "uom_id", "lead_id.wharehouse_id")
    def _compute_qty_available(self):
        for line in self:
            location = line.lead_id.wharehouse_id
            if not line.product_id or not location:
                line.qty_available = 0.0
                continue
            product = line.product_id.with_context(location=location.id)
            qty = product.free_qty
            # Convertir a la unidad de medida de la línea si es distinta
            if line.uom_id and line.uom_id != product.uom_id:
                qty = product.uom_id._compute_quantity(qty, line.uom_id)
            line.qty_available = qty

    def _get_free_qty(self, locations):
        """Cantidad libre del producto en las ubicaciones dadas, en la UdM de la línea."""
        self.ensure_one()
        if not self.product_id or not locations:
            return 0.0
        product = self.product_id.sudo().with_context(location=locations.ids)
        qty = product.free_qty
        if self.uom_id and self.uom_id != self.product_id.uom_id:
            qty = self.product_id.uom_id._compute_quantity(qty, self.uom_id)
        return qty

    @api.depends("product_id", "uom_id", "lead_id.company_id")
    def _compute_qty_scopes(self):
        Warehouse = self.env["stock.warehouse"].sudo()
        cache = {}  # evita repetir búsquedas para la misma empresa

        def locations_for(domain_key, domain):
            if domain_key not in cache:
                cache[domain_key] = Warehouse.search(domain).view_location_id
            return cache[domain_key]

        for line in self:
            branch = line.lead_id.company_id or self.env.company
            root = branch.root_id

            branch_locs = locations_for(
                ("branch", branch.id), [("company_id", "=", branch.id)]
            )
            company_locs = locations_for(
                ("company", root.id), [("company_id", "child_of", root.id)]
            )

            line.qty_available_branch = line._get_free_qty(branch_locs)
            line.qty_available_company = line._get_free_qty(company_locs)

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
            if line.lead_id._has_material_output():
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
                if lead._has_material_output():
                    raise ValidationError(_(
                        "No se pueden agregar líneas de material una vez "
                        "generada la salida de inventario."
                    ))
        return super().create(vals_list)
