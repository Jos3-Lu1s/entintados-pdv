from odoo import fields, models, api, _
from odoo.exceptions import UserError

class StockPicking(models.Model):
    _inherit = "stock.picking"

    crm_lead_id = fields.Many2one(
        comodel_name="crm.lead",
        string="Oportunidad CRM",
        index=True,
        copy=False,
        ondelete="set null",
    )
    
    material_approval_state = fields.Selection([
        ('to_approve', 'Pendiente de aprobación'),
        ('approved', 'Aprobado'),
        ('refused', 'Rechazado'),
    ], string="Aprobación de salida", default='to_approve', copy=False, tracking=True)

    material_approver_id = fields.Many2one(
        'res.users',
        string="Aprobador (jefe directo)",
        compute="_compute_material_approver_id",
        store=True,
    )

    is_material_approver = fields.Boolean(
        compute="_compute_is_material_approver",
    )

    @api.depends('crm_lead_id.user_id')
    def _compute_material_approver_id(self):
        for picking in self:
            approver = False
            salesperson = picking.crm_lead_id.user_id
            employee = salesperson.employee_id if salesperson else False
            if employee and employee.parent_id and employee.parent_id.user_id:
                approver = employee.parent_id.user_id
            picking.material_approver_id = approver

    def _compute_is_material_approver(self):
        for picking in self:
            picking.is_material_approver = bool(
                picking.material_approver_id
                and self.env.user == picking.material_approver_id
            )

    def action_approve_material(self):
        for picking in self:
            if picking.material_approver_id and self.env.user != picking.material_approver_id \
               and not self.env.user.has_group('base.group_system'):
                raise UserError(_("Solo el jefe directo asignado puede aprobar esta salida."))
            picking.material_approval_state = 'approved'
            picking.activity_ids.filtered(
                lambda a: a.summary == 'Aprobación de salida de material'
            ).action_feedback(feedback=_('Salida aprobada.'))

    def action_refuse_material(self):
        for picking in self:
            if picking.material_approver_id and self.env.user != picking.material_approver_id \
               and not self.env.user.has_group('base.group_system'):
                raise UserError(_("Solo el jefe directo asignado puede rechazar esta salida."))
            picking.material_approval_state = 'refused'
            picking.activity_ids.filtered(
                lambda a: a.summary == 'Aprobación de salida de material'
            ).action_feedback(feedback=_('Salida rechazada.'))

    def button_validate(self):
        for picking in self:
            if picking.crm_lead_id and picking.material_approval_state != 'approved':
                raise UserError(_(
                    "Esta salida requiere la aprobación del jefe directo del "
                    "vendedor antes de poder validarse."
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
        
    def _get_material_report_values(self):
        self.ensure_one()
        lead = self.crm_lead_id
        return {
            'lead': lead,
            'picking': self,
            'partner': self.partner_id or lead.partner_id,
            'salesperson': lead.user_id,
            'approver': self.material_approver_id,
            'lines': lead.material_line_ids if lead else self.env['crm.material.line'],
            'date': self.scheduled_date or fields.Date.context_today(self),
        }
    