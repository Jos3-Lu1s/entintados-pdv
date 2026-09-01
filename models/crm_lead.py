from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

DEMO_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_demo'
APPROVAL_CATEGORY_XMLID = 'entintados_pdv.approval_category_salida_material'

class CrmLead(models.Model):
    _inherit = 'crm.lead'

    stage_type = fields.Selection(
        related='stage_id.stage_type',
        string='Tipo de Etapa',
        store=True,
    )
    
    draft_quotation_count = fields.Integer(
        compute='_compute_draft_quotation_count',
        string='Cotizaciones en Borrador'
    )
    
    material_line_ids = fields.One2many(
        "crm.material.line",
        "lead_id",
        string="Solicitudes de materiales",
    )
    
    approval_request_id = fields.Many2one(
        'approval.request',
        string="Solicitud de Aprobación",
        copy=False,
    )

    approval_request_status = fields.Selection(
        related='approval_request_id.request_status',
        string="Estado de Aprobación",
        store=True,
    )
    
    approval_state = fields.Selection(
        related="approval_request_id.request_status",
        string="Estado de aprobación",
        readonly=True,
    )

    def _prepare_customer_values(self, partner_name, is_company=False, parent_id=False):
        res = super()._prepare_customer_values(partner_name, is_company=is_company, parent_id=parent_id)
        res['is_customer'] = True
        return res
    
    def write(self, vals):
        if 'stage_id' in vals and not self.env.context.get('skip_stage_sequence_check'):
            new_stage = self.env['crm.stage'].browse(vals['stage_id'])
            for record in self:
                old_stage = record.stage_id
                if not old_stage or not new_stage or old_stage.id == new_stage.id:
                    continue

                if new_stage.stage_type in ('quotation', 'closed'):
                    raise ValidationError(_(
                        'No puedes mover manualmente la oportunidad a "%s". '
                        'Esta etapa se asigna automáticamente por el flujo de ventas.'
                    ) % new_stage.name)

                ordered_stages = self.env['crm.stage'].search([], order='sequence, id')
                stage_ids = ordered_stages.ids

                if old_stage.id not in stage_ids or new_stage.id not in stage_ids:
                    continue

                old_index = stage_ids.index(old_stage.id)
                new_index = stage_ids.index(new_stage.id)

                quotation_stage = ordered_stages.filtered(lambda s: s.stage_type == 'quotation')
                quotation_index = stage_ids.index(quotation_stage.id) if quotation_stage else None

                if quotation_index is not None and old_index < quotation_index and new_index < quotation_index:
                    continue

                if new_index > old_index + 1:
                    skipped = ordered_stages[old_index + 1:new_index]
                    raise ValidationError(_(
                        'No puedes saltar etapas. Antes de pasar a "%s" '
                        'debes pasar por: %s'
                    ) % (new_stage.name, ', '.join(skipped.mapped('name'))))

                if quotation_index is not None and old_index >= quotation_index and new_index < old_index:
                    raise ValidationError(_(
                        'No puedes regresar de "%s" a una etapa anterior una vez que '
                        'la oportunidad llegó a Cotización.'
                    ) % old_stage.name)

        return super().write(vals)

    def _schedule_demo_activity(self):
        """Programa una actividad de demostración pendiente (evita duplicar si ya hay una abierta)."""
        self.ensure_one()
        activity_type = self.env.ref(DEMO_ACTIVITY_XMLID, raise_if_not_found=False)
        if not activity_type or self.activity_ids.filtered(lambda a: a.activity_type_id == activity_type):
            return
        self.activity_schedule(
            activity_type_id=activity_type.id,
            user_id=self.user_id.id or self.env.uid,
            summary=activity_type.summary,
            note=_('Se solicitó la salida de material para la demostración.'),
        )

    @api.constrains('stage_id', 'expected_revenue')
    def _check_expected_revenue_in_quotation(self):
        for lead in self:
            if lead.stage_id.stage_type == 'quotation' and not lead.expected_revenue:
                raise ValidationError(_(
                'No puedes mover la oportunidad a la etapa "%s" sin un '
                'ingreso esperado (importe de la cotización) mayor a cero.'
            ) % lead.stage_id.name)
            
    @api.depends('order_ids.state')
    def _compute_draft_quotation_count(self):
        for lead in self:
            lead.draft_quotation_count = len(lead.order_ids.filtered(lambda o: o.state == 'draft'))
            
    def action_view_sale_quotation(self):
        action = super().action_view_sale_quotation()
        domain = action.get('domain', [])
        if isinstance(domain, list):
            domain = domain + [('state', '!=', 'cancel')]
        action['domain'] = domain
        return action
    
    def action_request_material_output(self):
        self.ensure_one()
        if not self.material_line_ids:
            raise UserError(_("No hay líneas de material para solicitar salida."))
        if self.picking_count > 0:
            raise UserError(_("Ya existe una salida de inventario generada para esta oportunidad."))
        if self.approval_request_id:
            raise UserError(_("Ya existe una solicitud de aprobación en curso para esta oportunidad."))

        category = self.env.ref(APPROVAL_CATEGORY_XMLID, raise_if_not_found=False)
        if not category:
            raise UserError(_(
                "No se encontró la categoría de aprobación 'Solicitud de salida de material'."
            ))

        approver = self._get_material_approver()
        if not approver:
            raise UserError(_(
                "No se pudo determinar el jefe directo del vendedor. "
                "Verifica el organigrama en Empleados."
            ))

        first_line = self.material_line_ids[:1]

        approval_request = self.env['approval.request'].create({
            'name': _('Salida de material - %s') % self.name,
            'category_id': category.id,
            'request_owner_id': self.user_id.id or self.env.uid,
            'partner_id': self.partner_id.id,
            'date': fields.Date.context_today(self),
            'crm_lead_id': self.id,
            'reason': '\n'.join(
                '%s x %s %s' % (line.quantity, line.uom_id.name or '', line.product_id.display_name)
                for line in self.material_line_ids
            ),
            'approver_ids': [(0, 0, {'user_id': approver.id})],
            'product_line_ids': [
                (0, 0, {
                    'product_id': line.product_id.id,
                    'quantity': line.quantity,
                    'description': line.description,
                })
                for line in self.material_line_ids
            ],
        })
        approval_request.action_confirm()

        self.approval_request_id = approval_request.id
        self._schedule_demo_activity()

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'approval.request',
            'view_mode': 'form',
            'res_id': approval_request.id,
        }
        
    
        
    picking_ids = fields.One2many(
        comodel_name="stock.picking",
        inverse_name="crm_lead_id",
        string="Salidas",
    )

    picking_count = fields.Integer(
        string="Salidas",
        compute="_compute_picking_count",
    )

    @api.depends("picking_ids")
    def _compute_picking_count(self):
        for lead in self:
            lead.picking_count = len(lead.picking_ids)

    def action_view_pickings(self):
        self.ensure_one()

        action = {
            "type": "ir.actions.act_window",
            "name": "Salidas",
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [
                ("crm_lead_id", "=", self.id),
            ],
            "context": {
                "default_crm_lead_id": self.id,
                "default_partner_id": self.partner_id.id,
            },
        }

        # Si solamente existe una salida,
        # abrirla directamente en formulario.
        if self.picking_count == 1:
            action.update({
                "view_mode": "form",
                "res_id": self.picking_ids.id,
            })

        return action

    def _get_user_digital_signature(self, user):
        if not user:
            return False
        if getattr(user, 'digital_signature', None):
            return user.digital_signature
        if getattr(user, 'sign_signature', None):
            return user.sign_signature
        if user.partner_id and getattr(user.partner_id, 'signature', None):
            return user.partner_id.signature
        employee = self.env['hr.employee'].search([('user_id', '=', user.id)], limit=1)
        if employee and getattr(employee, 'signature', None):
            return employee.signature
        return False
    
    def _get_material_approver(self):
        self.ensure_one()
        if not self.user_id:
            return self.env['res.users']
        employee = self.env['hr.employee'].search([('user_id', '=', self.user_id.id)], limit=1)
        if employee and employee.parent_id and employee.parent_id.user_id:
            return employee.parent_id.user_id
        return self.env['res.users']
    
    def action_view_approval_request(self):
        self.ensure_one()
        if not self.approval_request_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': _('Solicitud de Aprobación'),
            'res_model': 'approval.request',
            'view_mode': 'form',
            'res_id': self.approval_request_id.id,
            'target': 'current',
        }

class CrmStage(models.Model):
    _inherit = 'crm.stage'

    stage_type = fields.Selection([
        ('new', 'Prospecto'),
        ('visit', 'Visita de campo'),
        ('demo', 'Demo'),
        ('quotation', 'Cotizacion'),
        ('closed', 'Venta Cerrada'),
        ('postsale', 'Post Venta')
        ])
    
    @api.constrains('stage_type')
    def _check_stage_type_unique(self):
        for record in self:
            if record.stage_type:
                duplicate = self.search([
                    ('stage_type', '=', record.stage_type),
                    ('id', '!=', record.id)
                ], limit=1)
                if duplicate:
                    raise ValidationError((
                        'El tipo "%s" ya está asignado a la etapa "%s". '
                        'Cada tipo solo puede usarse en una etapa.'
                    ) % (dict(record._fields['stage_type'].selection).get(record.stage_type), duplicate.name))