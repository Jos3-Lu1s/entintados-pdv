import base64
import unicodedata
from odoo import fields, models, api, _
from odoo.exceptions import UserError, ValidationError

MATERIAL_OUTPUT_TYPE_XMLID = 'entintados_pdv.picking_type_material_output'
AUDIT_DEPARTMENT_XMLID = 'entintados_pdv.hr_department_auditoria'
APPROVE_MATERIAL_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_material_output'
DEMO_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_demo'

class StockMove(models.Model):
    _inherit = 'stock.move'

    material_cost_currency_id = fields.Many2one(related='company_id.currency_id')
    material_unit_cost = fields.Monetary(
        string='Costo unitario', currency_field='material_cost_currency_id', compute='_compute_material_cost')
    material_total_cost = fields.Monetary(
        string='Costo total', currency_field='material_cost_currency_id', compute='_compute_material_cost')

    @api.depends('product_id.standard_price', 'product_id.uom_id', 'product_uom',
                 'product_uom_qty', 'quantity', 'state', 'company_id')
    def _compute_material_cost(self):
        for move in self:
            product = move.product_id.with_company(move.company_id or self.env.company)
            move.material_unit_cost = product.uom_id._compute_price(
                product.standard_price, move.product_uom or product.uom_id) if product else 0.0
            quantity = move.quantity if move.state == 'done' else move.product_uom_qty
            move.material_total_cost = move.material_unit_cost * quantity


class StockPicking(models.Model):
    _inherit = "stock.picking"

    is_crm_demo_output = fields.Boolean(compute='_compute_is_crm_demo_output')
    can_cancel_demo = fields.Boolean(compute='_compute_can_cancel_demo')

    @api.depends_context('uid')
    @api.depends('crm_lead_id', 'approval_request_id.material_auditor_ids')
    def _compute_can_cancel_demo(self):
        for picking in self:
            lead = picking.crm_lead_id or picking.approval_request_id.crm_lead_id
            picking.can_cancel_demo = bool(lead and lead._can_cancel_demo())

    def _cancel_rejected_demo(self):
        self.ensure_one()
        self.write({'material_approval_state': 'refused'})
        self.activity_ids.filtered(lambda activity: activity.summary ==
            'Validación de Auditoría - Salida de material').action_feedback(feedback=_('Rechazado por Auditoría.'))
        return super(StockPicking, self).action_cancel()

    @api.depends('picking_type_id', 'crm_lead_id', 'approval_request_id.crm_lead_id')
    def _compute_is_crm_demo_output(self):
        for picking in self:
            picking.is_crm_demo_output = bool(picking._is_material_output_type() and
                (picking.crm_lead_id or picking.approval_request_id.crm_lead_id))

    def _cancel_demo_document(self):
        return super(StockPicking, self).action_cancel()

    def action_cancel(self):
        for picking in self.filtered('is_crm_demo_output'):
            if not picking.can_cancel_demo:
                raise UserError(_('Solo el jefe del organigrama o los auditores asignados pueden cancelar la demostración.'))
        result = super().action_cancel()
        cancelled = self.filtered(lambda picking: picking.is_crm_demo_output and picking.state == 'cancel')
        leads = cancelled.crm_lead_id | cancelled.approval_request_id.crm_lead_id
        leads._mark_demo_lost(_('Cancelación de la salida de material de la demostración.'))
        return result

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
    
    """ Campos de evidencia de salida de material y de uso del material """
    document_file = fields.Binary(
        string="Documento de entrega de material",
        attachment=True,
    )

    document_filename = fields.Char(
        string="Nombre del documento",
    )
    
    evidence_attachment_ids = fields.Many2many(
        "ir.attachment",
        relation="stock_picking_evidence_attachment_rel",
        column1="picking_id",
        column2="attachment_id",
        string="Evidencias",
    )
    
    evidence_use_attachment_ids = fields.Many2many(
        "ir.attachment",
        relation="stock_picking_evidence_use_attachment_rel",
        column1="picking_id",
        column2="attachment_id",
        string="Uso del material",
    )
    
    validate_delivery = fields.Boolean(
        string="Entrega validada",
    )
    
    validate_material_use = fields.Boolean(
        string="Uso de material validado",
    )

    can_validate_material_evidence = fields.Boolean(
        string='Puede validar evidencias', compute='_compute_can_validate_material_evidence')

    @api.depends_context('uid', 'company')
    @api.depends('company_id')
    def _compute_can_validate_material_evidence(self):
        employees = self.env['hr.employee'].sudo().search([
            ('user_id', '=', self.env.uid), ('active', '=', True),
            ('company_id', 'in', self.company_id.ids),
        ])
        audit_department = self.env.ref(AUDIT_DEPARTMENT_XMLID, raise_if_not_found=False)
        allowed_companies = set()
        for employee in employees:
            department = employee.department_id
            normalized = ''.join(char for char in unicodedata.normalize(
                'NFKD', department.name or '') if not unicodedata.combining(char)).strip().casefold()
            if department and (department == audit_department or normalized in {
                    'auditoria', 'auxiliar de auditoria', 'auxiliar auditoria',
                    'auxiliares de auditoria'}):
                allowed_companies.add(employee.company_id.id)
        for picking in self:
            picking.can_validate_material_evidence = picking.company_id.id in allowed_companies

    def _check_material_evidence_validation(self, use=False):
        for picking in self:
            if not picking.can_validate_material_evidence:
                raise UserError(_('Solo personal de Auditoría o Auxiliar de Auditoría puede validar las evidencias.'))
            if use:
                if picking.state != 'done':
                    raise UserError(_("Solo se puede validar el uso de material de salidas que estén en estado 'Hecho'."))
                if not picking.validate_delivery:
                    raise UserError(_('Debe validar la entrega antes de validar el uso de material.'))
                if not picking.evidence_use_attachment_ids:
                    raise UserError(_('Debe adjuntar al menos una evidencia de uso de material antes de validar el uso.'))
            else:
                if picking.state not in ('assigned', 'done'):
                    raise UserError(_("Solo se puede validar la entrega de salidas en estado 'Listo' o 'Hecho'."))
                if not picking.evidence_attachment_ids:
                    raise UserError(_('Debe adjuntar al menos una evidencia de entrega antes de validar la entrega.'))

    def write(self, vals):
        if {'document_file', 'document_filename'}.intersection(vals) and self.filtered('validate_delivery'):
            raise UserError(_('No se puede modificar el documento después de validar la entrega.'))
        if vals.get('validate_delivery'):
            self._check_material_evidence_validation()
        if vals.get('validate_material_use'):
            self._check_material_evidence_validation(use=True)
        return super().write(vals)

    def _action_done(self):
            res = super()._action_done()
            return res
                    
    def _generate_material_document(self):
        self.ensure_one()

        report = self.env.ref(
            'entintados_pdv.action_report_material_stock_picking',
            raise_if_not_found=False,
        )
        if not report:
            return

        pdf_content, _report_format = report._render_qweb_pdf(
            report.report_name, [self.id]
        )
        if not isinstance(pdf_content, bytes):
            return

        self.write({
            'document_file': base64.b64encode(pdf_content),
            'document_filename': 'Solicitud_Material_%s.pdf' % (self.name or self.id),
        })
    
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
        """ department = self.env.ref(AUDIT_DEPARTMENT_XMLID, raise_if_not_found=False)"""
        self.ensure_one()
        if not self.approval_request_id:
            return False
        return self.env.user in self.approval_request_id.sudo().material_auditor_ids.user_id
    
    @api.depends_context('uid')
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
        return False

    def action_audit_approve_material(self):
        activity_type = self.env.ref(APPROVE_MATERIAL_ACTIVITY_XMLID, raise_if_not_found=False)

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

            if activity_type:
                picking.activity_ids.filtered(
                    lambda a: a.activity_type_id == activity_type
                ).action_feedback(feedback=_('Validado por Auditoría.'))

        return self.button_validate()

    def action_audit_refuse_material(self):
        self.ensure_one()
        if self.is_crm_demo_output:
            self.check_access('read')
            if not self._is_current_user_auditor():
                raise UserError(_('Solo los auditores asignados pueden rechazar esta salida.'))
            if self.material_approval_state != 'to_audit' or self.state in ('done', 'cancel'):
                raise UserError(_('Solo puedes rechazar una salida pendiente de auditoría.'))
            action = self.approval_request_id.sudo()._reject_demo()
            self.sudo().write({'material_auditor_id': self.env.uid})
            return action
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
                
    def action_validate_delivery(self):
        self._check_material_evidence_validation()
        for picking in self:
            picking.write({
                'validate_delivery': True,
            })
    
    def action_validate_material_use(self):
        self._check_material_evidence_validation(use=True)
        demo_activity_type = self.env.ref(DEMO_ACTIVITY_XMLID, raise_if_not_found=False)

        
        for picking in self:
            if picking.state != 'done':
                raise UserError(_("Solo se puede validar el uso de material de salidas que estén en estado 'Hecho'."))
            if not picking.validate_delivery:
                raise UserError(_("Debe validar la entrega antes de validar el uso de material."))
            if not picking.evidence_use_attachment_ids:
                raise UserError(_("Debe adjuntar al menos una evidencia de uso de material antes de validar el uso."))
            picking.write({
                'validate_material_use': True,
            })
    
            if picking.crm_lead_id:
                picking.crm_lead_id.write({
                    'demo_end': True,
                })
                if demo_activity_type:
                    picking.crm_lead_id.activity_ids.filtered(
                        lambda a: a.activity_type_id == demo_activity_type
                    ).action_feedback(feedback=_('Demostración finalizada, uso de material validado.'))
