from odoo import models, fields, api


class ApprovalsProductLine(models.Model):
    _inherit = 'approval.product.line'

    description = fields.Text('Descripción')
    
    qty_available = fields.Float(
        string="Disp. almacén",
        compute="_compute_qty_scopes",
        digits="Product Unit of Measure",
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

    @api.depends("product_id", "product_uom_id", "approval_request_id.company_id")
    def _compute_qty_scopes(self):
        Warehouse = self.env["stock.warehouse"].sudo()

        # Oportunidad de origen de cada solicitud (una sola búsqueda)
        leads = self.env["crm.lead"].sudo().with_context(active_test=False).search([
            ("approval_request_id", "in", self.approval_request_id.ids),
        ])
        lead_by_request = {lead.approval_request_id.id: lead for lead in leads}

        cache = {}

        def locations_for(key, domain):
            if key not in cache:
                cache[key] = Warehouse.search(domain).view_location_id
            return cache[key]

        for line in self:
            product = line.product_id
            if not product:
                line.qty_available = 0.0
                line.qty_available_branch = 0.0
                line.qty_available_company = 0.0
                continue

            branch = line.approval_request_id.company_id or self.env.company
            root = branch.root_id

            branch_locs = locations_for(("branch", branch.id), [("company_id", "=", branch.id)])
            company_locs = locations_for(("company", root.id), [("company_id", "child_of", root.id)])

            lead = lead_by_request.get(line.approval_request_id.id)
            warehouse_locs = lead.wharehouse_id if lead and lead.wharehouse_id else branch_locs

            uom = line.product_uom_id
            line.qty_available = product._get_free_qty_in(warehouse_locs, uom)
            line.qty_available_branch = product._get_free_qty_in(branch_locs, uom)
            line.qty_available_company = product._get_free_qty_in(company_locs, uom)
