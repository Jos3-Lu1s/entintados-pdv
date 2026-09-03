from odoo import fields, models, api, _
from odoo.exceptions import UserError, ValidationError

MATERIAL_OUTPUT_TYPE_XMLID = 'entintados_pdv.picking_type_material_output'
AUDIT_DEPARTMENT_XMLID = 'entintados_pdv.hr_department_auditoria'

class StockPicking(models.Model):
    _inherit = "stock.picking"

    crm_lead_id = fields.Many2one(
        comodel_name="crm.lead",
        string="Oportunidad CRM",
        index=True,
        copy=False,
        ondelete="set null",
    )

    approval_request_id = fields.Many2one(
        comodel_name="approval.request",
        string="Solicitud de Aprobación",
        index=True,
        copy=False,
        ondelete="set null",
    )
    
    material_approval_state = fields.Selection([
        ('to_audit', 'Pendiente de validación de Auditoría'),
        ('approved', 'Aprobado'),
        ('refused', 'Rechazado'),
    ], string="Aprobación de salida", default='to_audit', copy=False, tracking=True)

    is_material_approver = fields.Boolean(
        compute="_compute_is_material_approver",
    )

    signature = fields.Binary(
        string="Firma del Gerente",
        copy=False,
        attachment=True,
        help="Firma digital del gerente/jefe directo que aprueba la salida de material.",
    )
    signature_date = fields.Datetime(
        string="Fecha de Firma",
        copy=False,
        readonly=True,
    )
    
    is_material_output_type = fields.Boolean(
        compute="_compute_is_material_output_type",
    )
    
    material_auditor_id = fields.Many2one(
        'res.users',
        string="Validado por (Auditoría)",
        copy=False,
        readonly=True,
    )
    
    is_material_auditor = fields.Boolean(
        compute="_compute_is_material_auditor",
    )
    
    @api.depends('picking_type_id')
    def _compute_is_material_output_type(self):
        for picking in self:
            picking.is_material_output_type = picking._is_material_output_type()
    
    def _is_material_output_type(self):
        self.ensure_one()
        material_type = self.env.ref(MATERIAL_OUTPUT_TYPE_XMLID, raise_if_not_found=False)
        return bool(material_type and self.picking_type_id == material_type)
            
    def _is_current_user_auditor(self):
        """True si el usuario actual pertenece al departamento de Auditoría."""
        department = self.env.ref(AUDIT_DEPARTMENT_XMLID, raise_if_not_found=False)
        if not department:
            return False
        employee = self.env['hr.employee'].search(
            [('user_id', '=', self.env.user.id)], limit=1
        )
        return bool(employee and employee.department_id == department)
    
    def _compute_is_material_auditor(self):
        for picking in self:
            picking.is_material_auditor = bool(
                picking.is_material_output_type and picking._is_current_user_auditor()
            )
            
    def _compute_is_material_approver(self):
        for picking in self:
            picking.is_material_approver = picking.is_material_auditor    

    def _get_auditor_signature(self, auditor):
        if not auditor:
            return False
        if getattr(auditor, 'digital_signature', None):
            return auditor.digital_signature
        if getattr(auditor, 'sign_signature', None):
            return auditor.sign_signature
        if auditor.partner_id and getattr(auditor.partner_id, 'signature', None):
            return auditor.partner_id.signature
        employee = self.env['hr.employee'].search([('user_id', '=', auditor.id)], limit=1)
        if employee and getattr(employee, 'signature', None):
            return employee.signature
        return False

    def action_audit_approve_material(self):
        for picking in self:
            if not picking.is_material_output_type:
                raise UserError(_("Esta acción solo aplica a salidas de tipo 'Salida de material'."))
            if picking.material_approval_state != 'to_audit':
                raise UserError(_("Esta salida no está pendiente de validación de Auditoría."))
            if not picking._is_current_user_auditor() and not self.env.user.has_group('base.group_system'):
                raise UserError(_(
                    "Solo personal del departamento de Auditoría puede validar esta salida."
                ))

            auto_sig = picking._get_auditor_signature(self.env.user)
            vals = {
                'material_approval_state': 'approved',
                'material_auditor_id': self.env.user.id,
            }
            if auto_sig and not picking.signature:
                vals['signature'] = auto_sig
            if not picking.signature_date:
                vals['signature_date'] = fields.Datetime.now()
            picking.write(vals)

            picking.activity_ids.filtered(
                lambda a: a.summary == 'Validación de Auditoría - Salida de material'
            ).action_feedback(feedback=_('Validado por Auditoría.'))
            
        return self.button_validate()

    def action_audit_refuse_material(self):
        for picking in self:
            if not picking.is_material_output_type:
                raise UserError(_("Esta acción solo aplica a salidas de tipo 'Salida de material'."))
            if not picking._is_current_user_auditor() and not self.env.user.has_group('base.group_system'):
                raise UserError(_(
                    "Solo personal del departamento de Auditoría puede rechazar esta salida."
                ))
            picking.write({
                'material_approval_state': 'refused',
                'material_auditor_id': self.env.user.id,
            })
            picking.activity_ids.filtered(
                lambda a: a.summary == 'Validación de Auditoría - Salida de material'
            ).action_feedback(feedback=_('Rechazado por Auditoría.'))

    def button_validate(self):
        for picking in self:
            if picking._is_material_output_type() and picking.material_approval_state != 'approved':
                raise UserError(_(
                    "Esta salida requiere la aprobación de auditoria antes de poder ser validada."
                ))
        return super().button_validate()

    def action_view_crm_lead(self):
        self.ensure_one()

        if not self.crm_lead_id:
            return False

        return {
            "type": "ir.actions.act_window",
            "name": "Oportunidad",
            "res_model": "crm.lead",
            "view_mode": "form",
            "res_id": self.crm_lead_id.id,
            "target": "current",
        }

    def action_view_approval_request(self):
        self.ensure_one()

        if not self.approval_request_id:
            return False

        return {
            "type": "ir.actions.act_window",
            "name": "Solicitud de Aprobación",
            "res_model": "approval.request",
            "view_mode": "form",
            "res_id": self.approval_request_id.id,
            "target": "current",
        }
        
    def _get_material_report_values(self):
        self.ensure_one()
        lead = self.crm_lead_id
        salesperson = (lead.user_id if lead else False) or self.user_id or self.create_uid
        return {
            'lead': lead,
            'picking': self,
            'partner': self.partner_id or (lead.partner_id if lead else False),
            'salesperson': salesperson,
            'approver': self.material_auditor_id,
            'lines': self.move_ids,
            'date': self.scheduled_date or fields.Date.context_today(self),
            'approval_state': self.material_approval_state,
            'signature': self.signature,
            'signature_date': self.signature_date,
        }

    @api.constrains('picking_type_id', 'crm_lead_id', 'approval_request_id')
    def _check_material_output_requires_approval_flow(self):
        for picking in self:
            if picking._is_material_output_type() and not picking.approval_request_id:
                raise ValidationError(_(
                    "No se puede crear ni modificar una salida de tipo 'Salida de material' "
                    "sin que provenga del flujo de Solicitud de Aprobación desde una "
                    "oportunidad de CRM. Genera la salida desde la oportunidad correspondiente."
                ))
    