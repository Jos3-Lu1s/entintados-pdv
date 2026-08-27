from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

DEMO_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_demo'
FIELD_VISIT_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_field_visit'


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

                record._check_field_visit_before_leaving(old_stage, new_stage)

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

    def _ensure_material_request_allowed(self):
        """Valida que la oportunidad pueda solicitar salida de material."""
        self.ensure_one()
        if not self.material_line_ids:
            raise UserError(_("No hay líneas de material para solicitar salida."))
        if self.picking_count > 0:
            raise UserError(_("Ya existe una salida de inventario generada para esta oportunidad."))

    demo_meeting_scheduled = fields.Boolean(
        string="Demostración agendada",
        compute="_compute_meeting_scheduled",
        help="Verdadero cuando la demostración ya tiene una reunión con horario "
             "(inicio y fin) en el calendario.",
    )
    field_visit_scheduled = fields.Boolean(
        string="Visita de campo agendada",
        compute="_compute_meeting_scheduled",
        help="Verdadero cuando la visita de campo ya tiene una reunión con horario "
             "(inicio y fin) en el calendario.",
    )

    @api.depends(
        'activity_ids.activity_type_id',
        'activity_ids.calendar_event_id',
        'activity_ids.calendar_event_id.start',
        'activity_ids.calendar_event_id.stop',
        'activity_ids.calendar_event_id.allday',
    )
    def _compute_meeting_scheduled(self):
        demo_type = self.env.ref(DEMO_ACTIVITY_XMLID, raise_if_not_found=False)
        visit_type = self.env.ref(FIELD_VISIT_ACTIVITY_XMLID, raise_if_not_found=False)
        for lead in self:
            lead.demo_meeting_scheduled = lead._is_meeting_scheduled(demo_type)
            lead.field_visit_scheduled = lead._is_meeting_scheduled(visit_type)

    def _get_meeting_activity(self, activity_type):
        """Actividad (de reunión) del tipo dado en la oportunidad, o un recordset vacío."""
        self.ensure_one()
        if not activity_type:
            return self.env['mail.activity']
        return self.activity_ids.filtered(
            lambda a: a.activity_type_id == activity_type
        )[:1]

    def _is_meeting_scheduled(self, activity_type):
        """True si esa reunión ya tiene horario (evento de calendario con inicio y fin)."""
        self.ensure_one()
        event = self._get_meeting_activity(activity_type).calendar_event_id
        return bool(event and not event.allday and event.start and event.stop)

    def _get_or_create_meeting_activity(self, activity_type):
        """Garantiza que exista la actividad de reunión para enlazarla al calendario."""
        self.ensure_one()
        if not activity_type:
            return self.env['mail.activity']
        activity = self._get_meeting_activity(activity_type)
        if activity:
            return activity
        return self.activity_schedule(
            activity_type_id=activity_type.id,
            user_id=self.user_id.id or self.env.uid,
            summary=activity_type.summary,
        )

    def _action_schedule_meeting(self, activity_xmlid):
        """Abre el calendario para agendar (bloque de horario y campos) la reunión indicada."""
        self.ensure_one()
        activity_type = self.env.ref(activity_xmlid, raise_if_not_found=False)
        if not activity_type:
            raise UserError(_(
                "No está configurado el tipo de actividad requerido. "
                "Verifica que el módulo esté correctamente instalado/actualizado."
            ))
        activity = self._get_or_create_meeting_activity(activity_type)
        # Reutiliza el flujo nativo de Odoo (actividad de reunión -> calendario)
        # cuando está disponible; si no, abre el calendario con el evento enlazado.
        if hasattr(activity, 'action_create_calendar_event'):
            return activity.action_create_calendar_event()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'calendar.action_calendar_event'
        )
        action['context'] = {
            'default_activity_ids': [(6, 0, activity.ids)],
            'default_res_model': 'crm.lead',
            'default_res_id': self.id,
            'default_name': activity.summary or activity_type.name,
            'default_user_id': self.user_id.id or self.env.uid,
        }
        return action

    def action_schedule_demo_meeting(self):
        """Agenda la demostración en el calendario (requiere líneas de material)."""
        self.ensure_one()
        self._ensure_material_request_allowed()
        return self._action_schedule_meeting(DEMO_ACTIVITY_XMLID)

    def action_schedule_field_visit(self):
        """Agenda la visita de campo en el calendario."""
        self.ensure_one()
        return self._action_schedule_meeting(FIELD_VISIT_ACTIVITY_XMLID)

    def _check_field_visit_before_leaving(self, old_stage, new_stage):
        """Exige la visita de campo agendada (con horario) para avanzar de esa etapa."""
        if old_stage.stage_type != 'visit':
            return
        ordered_ids = self.env['crm.stage'].search([], order='sequence, id').ids
        if old_stage.id not in ordered_ids or new_stage.id not in ordered_ids:
            return
        if ordered_ids.index(new_stage.id) <= ordered_ids.index(old_stage.id):
            return  # no avanza (retrocede o permanece)
        if not self.field_visit_scheduled:
            raise ValidationError(_(
                "Antes de avanzar desde la etapa \"%s\" debes agendar la visita de "
                "campo en el calendario con horario de inicio y fin."
            ) % old_stage.name)

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
        """Genera la salida de material; exige que la demostración ya esté agendada."""
        self.ensure_one()
        self._ensure_material_request_allowed()
        if not self.demo_meeting_scheduled:
            raise UserError(_(
                "Primero agenda la demostración en el calendario "
                "(marca el horario de inicio y fin del evento)."
            ))
        return self._create_material_output()

    def _create_material_output(self):
        """Genera la salida de material (picking) con sus movimientos y abre el picking."""
        self.ensure_one()
        self._ensure_material_request_allowed()

        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', self.company_id.id or self.env.company.id)], limit=1
        )
        if not warehouse:
            raise UserError(_("No se encontró un almacén configurado."))

        picking_type = self.env.ref(
            'entintados_pdv.picking_type_material_output', raise_if_not_found=False
        )
        if not picking_type:
            raise UserError(_(
                "No se encontró el tipo de operación 'Salida de material'. "
                "Verifica que el módulo esté correctamente instalado/actualizado."
            ))

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'origin': self.name,
            'partner_id': self.partner_id.id,
            'crm_lead_id': self.id,
        })
        
        if picking.material_approver_id:
            picking.activity_schedule(
                activity_type_id=self.env.ref('mail.mail_activity_data_todo').id,
                user_id=picking.material_approver_id.id,
                summary='Aprobación de salida de material',
                note=_('La salida %s requiere tu aprobación como jefe directo del vendedor.') % picking.name,
            )

        for line in self.material_line_ids:
            self.env['stock.move'].create({
                'description_picking': line.description,
                'product_id': line.product_id.id,
                'product_uom_qty': line.quantity,
                'product_uom': line.uom_id.id,
                'picking_id': picking.id,
                'location_id': picking_type.default_location_src_id.id,
                'location_dest_id': picking_type.default_location_dest_id.id,
            })

        picking.action_confirm()

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': picking.id,
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
    
    def _get_material_report_values(self):
        self.ensure_one()
        picking = self.picking_ids[:1]
        approver = picking.material_approver_id if picking else self._get_material_approver()
        signature = picking._get_approver_signature() if picking else self._get_user_digital_signature(approver)
        return {
            'lead': self,
            'picking': picking,
            'partner': self.partner_id,
            'salesperson': self.user_id or self.create_uid,
            'approver': approver,
            'lines': self.material_line_ids,
            'date': fields.Date.context_today(self),
            'approval_state': picking.material_approval_state if picking else 'to_approve',
            'signature': signature,
            'signature_date': picking.signature_date if picking else False,
        }

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