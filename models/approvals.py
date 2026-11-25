from odoo import models, fields, api, _
from odoo.exceptions import RedirectWarning, UserError

MATERIAL_OUTPUT_TYPE_XMLID = 'entintados_pdv.picking_type_material_output'
APPROVAL_CATEGORY_XMLID = 'entintados_pdv.approval_category_salida_material'
APPROVE_MATERIAL_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_material_output'

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
    
class ApprovalApprover(models.Model):
    _inherit = 'approval.approver'

    def action_approve(self):
        for approver in self:
            request = approver.request_id
            if request._is_material_output_request() and not request.material_auditor_ids:
                action = self.env.ref('entintados_pdv.action_crm_material_auditor_assign_wizard')
                raise RedirectWarning(
                    _("Antes de aprobar esta solicitud debes asignar uno o más "
                      "auditores responsables de validar la salida de material."),
                    action.id,
                    _("Asignar auditores"),
                    {'default_approval_request_id': request.id},
                )

        return super().action_approve()
    
class ApprovalRequest(models.Model):
    _inherit = "approval.request"

    @api.model_create_multi
    def create(self, vals_list):
        prepared = [dict(vals) for vals in vals_list]
        names = []
        for vals in prepared:
            name = False
            if vals.get('crm_lead_id', self.env.context.get('default_crm_lead_id')):
                name = self.env.ref('entintados_pdv.seq_crm_approval_request').sudo().next_by_id()
                vals['name'] = name
            names.append(name)
        requests = super().create(prepared)
        for request, name in zip(requests, names):
            if name and request.name != name:
                request.name = name
        return requests

    def _cancel_demo_document(self):
        self.ensure_one()
        return super(ApprovalRequest, self).action_cancel()

    def action_cancel(self):
        demos = self.filtered('crm_lead_id')
        for request in demos:
            if not request.crm_lead_id._can_cancel_demo() and self.env.user not in request.sudo().material_auditor_ids.user_id:
                raise UserError(_('Solo el jefe del organigrama o los auditores asignados pueden cancelar la demostración.'))
        result = None
        if demos:
            result = super(ApprovalRequest, demos.sudo()).action_cancel()
        if self - demos:
            result = super(ApprovalRequest, self - demos).action_cancel()
        self.filtered(lambda request: request.request_status == 'cancel').crm_lead_id._mark_demo_lost(
            _('Cancelación de la aprobación de la demostración.'))
        return result

    can_cancel_demo = fields.Boolean(compute='_compute_demo_permissions')
    can_reject_demo = fields.Boolean(compute='_compute_demo_permissions')

    @api.depends_context('uid')
    @api.depends('crm_lead_id.user_id', 'material_auditor_ids', 'request_status')
    def _compute_demo_permissions(self):
        for request in self:
            lead = request.crm_lead_id
            request.can_cancel_demo = bool(lead and lead._can_cancel_demo())
            request.can_reject_demo = bool(lead and (self.env.su or
                self.env.user == lead.sudo()._get_material_approver()))

    def _reject_demo(self):
        self.ensure_one()
        request = self.sudo()
        pickings = request._get_material_pickings()
        if pickings.filtered(lambda picking: picking.state == 'done'):
            raise UserError(_('No puedes rechazar una demostración cuya salida ya fue realizada.'))
        action = self.crm_lead_id._return_demo_for_correction(self)
        # Call the native methods directly: rejection must never mark CRM lost.
        super(ApprovalRequest, request).action_refuse(request.approver_ids)
        for picking in pickings.filtered(lambda picking: picking.state != 'cancel'):
            picking._cancel_rejected_demo()
        request.message_post(body=_('Solicitud rechazada y devuelta a CRM para corregir los productos.'))
        return action

    def action_reject_demo(self):
        self.ensure_one()
        self.check_access('read')
        if not self.crm_lead_id or not self.can_reject_demo:
            raise UserError(_('Solo el jefe encargado del organigrama puede rechazar esta aprobación.'))
        if self.request_status != 'pending':
            raise UserError(_('Solo puedes rechazar una aprobación pendiente.'))
        return self._reject_demo()

    def action_refuse(self, approver=None):
        demos = self.filtered('crm_lead_id')
        for request in demos:
            request.action_reject_demo()
        ordinary = self - demos
        if ordinary:
            return super(ApprovalRequest, ordinary).action_refuse(approver)
    
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
    
    warehouse_id = fields.Many2one(
        'stock.location',
        string="Almacén",
    )
    
    material_auditor_ids = fields.Many2many(
        'hr.employee',
        string="Auditores asignados",
        copy=False,
        help="Empleados del departamento de Auditoría responsables de validar "
             "la salida de material generada a partir de esta solicitud.",
    )

    def _is_material_output_request(self):
        """Solicitudes de ``self`` que pertenecen a la categoría de salida de material."""
        category = self.env.ref(APPROVAL_CATEGORY_XMLID, raise_if_not_found=False)
        return self.filtered(lambda request: category and request.category_id == category)

    def _get_material_pickings(self):
        self.ensure_one()
        return self.picking_ids | self.generated_picking_id

    @api.depends('generated_picking_id', 'picking_ids')
    def _compute_picking_count(self):
        for request in self:
            request.picking_count = len(request._get_material_pickings())

    def action_view_picking(self):
        self.ensure_one()
        pickings = self._get_material_pickings()
        if not pickings:
            return False
        if len(pickings) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Salida de Almacén'),
                'res_model': 'stock.picking',
                'view_mode': 'form',
                'res_id': pickings.id,
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
    
    def action_approve(self, approver=None):
        # Si es la categoría de salida de material y AÚN no hay auditores
        # asignados, se interrumpe la aprobación y se abre el wizard.
        if len(self) == 1 and self._is_material_output_request() and not self.material_auditor_ids:
            action = self.env['ir.actions.act_window']._for_xml_id(
                'entintados_pdv.action_crm_material_auditor_assign_wizard')
            action['context'] = {'default_approval_request_id': self.id}
            return action

        res = super().action_approve(approver=approver)

        for request in self._is_material_output_request():
            if request.request_status == 'approved' and not request.generated_picking_id:
                # La aprobación la valida el flujo nativo. La salida es una
                # operación automática: no exige permisos de almacén al aprobador.
                request.sudo()._create_material_picking()
        return res

    def _create_material_picking(self):
        self.ensure_one()
        if not self._is_material_output_request():
            return False

        if not self.material_auditor_ids:
            raise UserError(_(
                "No se puede generar la salida de material sin auditores "
                "asignados a esta solicitud."
            ))

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
        partner = self.partner_id or lead.partner_id

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': self.warehouse_id.id,
            'origin': self.name or lead.name or '',
            'partner_id': partner.id,
            'approval_request_id': self.id,
            'crm_lead_id': lead.id,
        })

        if lead.material_line_ids:
            move_vals = [{
                'description_picking': line.description or line.product_id.display_name,
                'product_id': line.product_id.id,
                'product_uom_qty': line.quantity,
                'product_uom': line.uom_id.id,
                'picking_id': picking.id,
            } for line in lead.material_line_ids]
        else:
            move_vals = [{
                'description_picking': line.description or line.product_id.display_name,
                'product_id': line.product_id.id,
                'product_uom_qty': line.quantity or 1.0,
                'product_uom': (line.product_uom_id or line.product_id.uom_id).id,
                'picking_id': picking.id,
            } for line in self.product_line_ids.filtered('product_id')]
        self.env['stock.move'].create(move_vals)

        picking.action_confirm()
        self.generated_picking_id = picking.id
        self._notify_auditoria_department(picking)
        return picking

    def _notify_auditoria_department(self, picking):
        activity_type = self.env.ref(APPROVE_MATERIAL_ACTIVITY_XMLID, raise_if_not_found=False)
        note = _(
            'Se generó la salida de material %(picking)s relacionada a la '
            'oportunidad %(lead)s. Favor de revisar y aprobar.'
        ) % {
            'picking': picking.name or picking.id,
            'lead': self.crm_lead_id.name or '',
        }
        deadline = fields.Date.context_today(self)
        for user in self.material_auditor_ids.user_id:
            picking.activity_schedule(
                activity_type_id=activity_type.id if activity_type else False,
                user_id=user.id,
                summary=activity_type.summary if activity_type else _('Aprobar salida de material'),
                note=note,
                date_deadline=deadline,
            )
    
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
