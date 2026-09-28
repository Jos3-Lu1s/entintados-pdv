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

    employee_ids = fields.Many2many(
        'hr.employee',
        string="Auditores",
    )

    @api.depends_context('uid')
    def _compute_auditor_department_id(self):
        department = self.env.ref(AUDIT_DEPARTMENT_XMLID, raise_if_not_found=False)
        for record in self:
            record.auditor_department_id = department.id if department else False

    def action_confirm(self):
        self.ensure_one()
        if not self.employee_ids:
            raise UserError(_(
                "Debes seleccionar al menos un auditor para poder aprobar "
                "esta solicitud."
            ))
        self.approval_request_id.material_auditor_ids = [(6, 0, self.employee_ids.ids)]
        # Ahora sí ejecuta la aprobación real, ya con auditores asignados.
        return self.approval_request_id.action_approve()