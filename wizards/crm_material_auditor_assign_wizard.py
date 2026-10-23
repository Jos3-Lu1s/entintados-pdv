from odoo import models, fields, api, _
from odoo.exceptions import UserError

AUDIT_DEPARTMENT_XMLID = 'entintados_pdv.hr_department_auditoria'


class CrmMaterialAuditorAssignWizard(models.TransientModel):
    _name = 'crm.material.auditor.assign.wizard'
    _description = 'Asignar auditor(es) antes de aprobar salida de material'

    approval_request_id = fields.Many2one(
        'approval.request',
        string="Solicitud de aprobación",
        required=True,
        ondelete='cascade',
    )

    auditor_department_id = fields.Many2one(
        'hr.department',
        compute='_compute_auditor_department_id',
    )
    auditor_department_ids = fields.Many2many(
        'hr.department', compute='_compute_auditor_department_id')

    employee_ids = fields.Many2many(
        'hr.employee',
        string="Auditores",
    )

    @api.depends_context('uid')
    def _compute_auditor_department_id(self):
        department = self.env.ref(AUDIT_DEPARTMENT_XMLID, raise_if_not_found=False)
        auxiliary = self.env.ref('entintados_pdv.hr_department_auditor_auxiliar', raise_if_not_found=False)
        departments = self.env['hr.department'].browse()
        if department:
            departments |= department
        if auxiliary:
            departments |= auxiliary
        for record in self:
            record.auditor_department_id = department.id if department else False
            record.auditor_department_ids = departments

    def action_confirm(self):
        self.ensure_one()
        auditors = self.employee_ids
        if not auditors:
            departments = self.auditor_department_ids
            if not departments:
                raise UserError(_('No está configurado el departamento de Auditoría.'))
            auditors = self.env['hr.employee'].sudo().search([
                ('department_id', 'in', departments.ids), ('active', '=', True),
                ('user_id.active', '=', True), ('user_id.share', '=', False),
            ])
        if not auditors:
            raise UserError(_(
                "No hay auditores activos con usuario interno asignado en el departamento de Auditoría."
            ))
        self.approval_request_id.material_auditor_ids = [(6, 0, auditors.ids)]
        # Ahora sí ejecuta la aprobación real, ya con auditores asignados.
        return self.approval_request_id.action_approve()
