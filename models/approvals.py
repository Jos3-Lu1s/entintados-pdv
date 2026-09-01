from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

MATERIAL_OUTPUT_TYPE_XMLID = 'entintados_pdv.picking_type_material_output'
APPROVAL_CATEGORY_XMLID = 'entintados_pdv.approval_category_salida_material'

class Approval(models.Model):
    _inherit = 'approval.category'

    approval_type = fields.Selection(
        selection_add=[
            ("stock_out", "Crear salida de almacén"),
        ],
        ondelete={
            "stock_out": lambda records: records.write({
                "approval_type": "purchase"
            }),
        },
    )
    
class ApprovalRequest(models.Model):
    _inherit = "approval.request"
    
    crm_lead_id = fields.Many2one(
        'crm.lead',
        string="Oportunidad CRM",
        copy=False,
        index=True,
    )

    generated_picking_id = fields.Many2one(
        'stock.picking',
        string="Salida generada",
        copy=False,
        readonly=True,
    )

    picking_ids = fields.One2many(
        'stock.picking',
        'approval_request_id',
        string="Salidas de Almacén",
    )

    picking_count = fields.Integer(
        compute='_compute_picking_count',
        string="Cantidad de Salidas",
    )

    @api.depends('generated_picking_id', 'picking_ids')
    def _compute_picking_count(self):
        for request in self:
            pickings = request.picking_ids
            if request.generated_picking_id:
                pickings = pickings | request.generated_picking_id
            request.picking_count = len(pickings)

    def action_view_picking(self):
        self.ensure_one()
        pickings = self.picking_ids
        if self.generated_picking_id:
            pickings = pickings | self.generated_picking_id
        if not pickings:
            return False
        if len(pickings) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Salida de Almacén'),
                'res_model': 'stock.picking',
                'view_mode': 'form',
                'res_id': pickings[0].id if isinstance(pickings, list) else pickings.id,
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Salidas de Almacén'),
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', pickings.ids)],
            'target': 'current',
        }
    
    @api.depends('request_status', 'category_id')
    def _compute_check_material_output(self):
        """No calcula ningún campo visible: solo usa @api.depends como disparador.
        Se apoya en 'generated_picking_id' como guarda de idempotencia."""
        category_salida = self.env.ref(APPROVAL_CATEGORY_XMLID, raise_if_not_found=False)
        for request in self:
            if (
                request.request_status == 'approved'
                and not request.generated_picking_id
                and not self.env.context.get('skip_material_picking_trigger')
                and category_salida
                and request.category_id == category_salida
            ):
                request._create_material_picking()

    _material_output_trigger = fields.Boolean(
        compute='_compute_check_material_output',
        store=False,
    )

    def action_approve(self, approver=None):
        res = super().action_approve(approver=approver)
        category_salida = self.env.ref(APPROVAL_CATEGORY_XMLID, raise_if_not_found=False)
        for request in self:
            if (
                request.request_status == 'approved'
                and not request.generated_picking_id
                and category_salida
                and request.category_id == category_salida
            ):
                request._create_material_picking()
        return res

    def _create_material_picking(self):
        self.ensure_one()
        category_salida = self.env.ref(APPROVAL_CATEGORY_XMLID, raise_if_not_found=False)
        if not category_salida or self.category_id != category_salida:
            return False

        picking_type = self.env.ref(MATERIAL_OUTPUT_TYPE_XMLID, raise_if_not_found=False)
        if not picking_type:
            raise UserError(_(
                "No se encontró el tipo de operación 'Salida de material' (id: %s)."
            ) % MATERIAL_OUTPUT_TYPE_XMLID)
            
        self.env.cr.execute(
            "SELECT generated_picking_id FROM approval_request WHERE id = %s FOR UPDATE",
            (self.id,)
        )
        row = self.env.cr.fetchone()
        if row and row[0]:
            return False

        lead = self.crm_lead_id
        partner = self.partner_id or (lead.partner_id if lead else False)
        origin = self.name or (lead.name if lead else '')

        picking_vals = {
            'picking_type_id': picking_type.id,
            'origin': origin,
            'partner_id': partner.id if partner else False,
            'approval_request_id': self.id,
        }
        if lead:
            picking_vals['crm_lead_id'] = lead.id

        picking = self.env['stock.picking'].create(picking_vals)

        if lead and lead.material_line_ids:
            for line in lead.material_line_ids:
                self.env['stock.move'].create({
                    'description_picking': line.description or line.product_id.display_name,
                    'product_id': line.product_id.id,
                    'product_uom_qty': line.quantity,
                    'product_uom': line.uom_id.id,
                    'picking_id': picking.id,
                })
        elif self.product_line_ids:
            for line in self.product_line_ids:
                if not line.product_id:
                    continue
                uom_id = getattr(line, 'product_uom_id', False) or line.product_id.uom_id
                self.env['stock.move'].create({
                    'description_picking': line.description or line.product_id.display_name,
                    'product_id': line.product_id.id,
                    'product_uom_qty': line.quantity or 1.0,
                    'product_uom': uom_id.id if uom_id else False,
                    'picking_id': picking.id,
                })

        picking.action_confirm()
        self.generated_picking_id = picking.id
        return picking
    
    def action_view_crm_lead(self):
        self.ensure_one()
        if not self.crm_lead_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': _('Oportunidad'),
            'res_model': 'crm.lead',
            'view_mode': 'form',
            'res_id': self.crm_lead_id.id,
            'target': 'current',
        }
    