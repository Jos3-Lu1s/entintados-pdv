from odoo import fields, models, api, _
from odoo.exceptions import UserError

MATERIAL_OUTPUT_TYPE_XMLID = 'entintados_pdv.picking_type_material_output'

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
    
    @api.depends('picking_type_id')
    def _compute_is_material_output_type(self):
        for picking in self:
            picking.is_material_output_type = picking._is_material_output_type()
    
    def _is_material_output_type(self):
        self.ensure_one()
        material_type = self.env.ref(MATERIAL_OUTPUT_TYPE_XMLID, raise_if_not_found=False)
        return bool(material_type and self.picking_type_id == material_type)

    @api.depends('crm_lead_id.user_id', 'user_id', 'create_uid')
    def _compute_material_approver_id(self):
        for picking in self:
            approver = self.env['res.users']
            salesperson = picking.crm_lead_id.user_id or picking.user_id or picking.create_uid
            if salesperson:
                employee = self.env['hr.employee'].search([('user_id', '=', salesperson.id)], limit=1)
                if employee and employee.parent_id and employee.parent_id.user_id:
                    approver = employee.parent_id.user_id
            picking.material_approver_id = approver

    def _compute_is_material_approver(self):
        for picking in self:
            picking.is_material_approver = bool(
                picking.material_approver_id
                and self.env.user == picking.material_approver_id
            )

    def _get_approver_signature(self):
        self.ensure_one()
        if self.signature:
            return self.signature
        
        approver = self.material_approver_id
        if not approver and self.crm_lead_id:
            approver = self.crm_lead_id._get_material_approver()
            
        if approver:
            if getattr(approver, 'digital_signature', None):
                return approver.digital_signature
            if getattr(approver, 'sign_signature', None):
                return approver.sign_signature
            if approver.partner_id and getattr(approver.partner_id, 'signature', None):
                return approver.partner_id.signature
            employee = self.env['hr.employee'].search([('user_id', '=', approver.id)], limit=1)
            if employee and getattr(employee, 'signature', None):
                return employee.signature
        return False

    def action_approve_material(self):
        for picking in self:
            if not picking._is_material_output_type():
                raise UserError(_(
                    "Esta acción solo aplica a salidas de tipo 'Salida de material'."
                ))
            if picking.material_approver_id and self.env.user != picking.material_approver_id \
               and not self.env.user.has_group('base.group_system'):
                raise UserError(_(
                    "Solo el gerente o jefe directo (%s) del usuario que realiza la salida "
                    "puede aprobar y firmar este documento de salida."
                ) % picking.material_approver_id.name)
            
            auto_sig = picking._get_approver_signature()
            vals = {
                'material_approval_state': 'approved',
            }
            if auto_sig and not picking.signature:
                vals['signature'] = auto_sig
            if not picking.signature_date:
                vals['signature_date'] = fields.Datetime.now()
            picking.write(vals)
            
            picking.activity_ids.filtered(
                lambda a: a.summary == 'Aprobación de salida de material'
            ).action_feedback(feedback=_('Salida aprobada y firmada.'))

    def action_refuse_material(self):
        for picking in self:
            if not picking._is_material_output_type():
                raise UserError(_(
                    "Esta acción solo aplica a salidas de tipo 'Salida de material'."
                ))
            if picking.material_approver_id and self.env.user != picking.material_approver_id \
               and not self.env.user.has_group('base.group_system'):
                raise UserError(_(
                    "Solo el gerente o jefe directo (%s) del usuario que realiza la salida "
                    "puede rechazar este documento de salida."
                ) % picking.material_approver_id.name)
            picking.material_approval_state = 'refused'
            picking.activity_ids.filtered(
                lambda a: a.summary == 'Aprobación de salida de material'
            ).action_feedback(feedback=_('Salida rechazada.'))

    def button_validate(self):
        for picking in self:
            if picking._is_material_output_type() and picking.material_approval_state != 'approved':
                raise UserError(_(
                    "Esta salida requiere la aprobación y firma del gerente encargado "
                    "del usuario que realiza la salida antes de poder validarse."
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
        salesperson = (lead.user_id if lead else False) or self.user_id or self.create_uid
        return {
            'lead': lead,
            'picking': self,
            'partner': self.partner_id or (lead.partner_id if lead else False),
            'salesperson': salesperson,
            'approver': self.material_approver_id,
            'lines': self.move_ids,
            'date': self.scheduled_date or fields.Date.context_today(self),
            'approval_state': self.material_approval_state,
            'signature': self._get_approver_signature(),
            'signature_date': self.signature_date,
        }

    