from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

DEMO_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_demo'
FIELD_VISIT_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_field_visit'

APPROVAL_CATEGORY_XMLID = 'entintados_pdv.approval_category_salida_material'

class CrmLead(models.Model):
    _inherit = 'crm.lead'

    user_id = fields.Many2one(readonly=True)

    can_cancel_demo = fields.Boolean(compute='_compute_can_cancel_demo')

    def _can_cancel_demo(self):
        self.ensure_one()
        request = self.sudo().approval_request_id
        return bool(self.env.su or self.env.user == self.sudo()._get_material_approver()
                    or self.env.user in request.material_auditor_ids.user_id)

    @api.depends_context('uid')
    @api.depends('user_id', 'approval_request_id.material_auditor_ids')
    def _compute_can_cancel_demo(self):
        for lead in self:
            lead.can_cancel_demo = lead._can_cancel_demo()

    def _return_demo_for_correction(self, request):
        self.ensure_one()
        lead = self.sudo()
        if lead.approval_request_id != request or not lead.active or lead.demo_end:
            raise UserError(_('Esta solicitud ya no corresponde a la demostración activa.'))
        lead.write({'approval_request_id': False})
        lead.message_post(body=_('Demostración rechazada por %s. Corrige los productos y solicita nuevamente la aprobación.') % self.env.user.display_name)
        return {'type': 'ir.actions.act_window', 'res_model': 'crm.lead',
                'res_id': lead.id, 'view_mode': 'form', 'target': 'current'}

    def _mark_demo_lost(self, reason):
        # The caller already checked cancellation rights on the linked document.
        # Auditors/warehouse users need not have general CRM write permissions.
        for lead in self.sudo():
            requests = lead._get_demo_approvals()
            for request in requests.filtered(lambda request: request.request_status != 'cancel'):
                request._cancel_demo_document()
            for picking in lead._get_demo_pickings().filtered(
                    lambda picking: picking._is_material_output_type() and picking.state not in ('done', 'cancel')):
                picking._cancel_demo_document()
            if lead.active or lead.probability != 0:
                lead.action_set_lost()
                lead.message_post(body=reason)

    def action_cancel_demo(self):
        self.ensure_one()
        self.check_access('read')
        if not self._can_cancel_demo():
            raise UserError(_('Solo el jefe del organigrama o los auditores asignados pueden cancelar la demostración.'))
        self = self.sudo()
        if self.demo_end:
            raise UserError(_('No puedes cancelar una demostración que ya está finalizada.'))
        if self.stage_type != 'demo' and not self.approval_request_id:
            raise UserError(_('Esta oportunidad no tiene una demostración en curso.'))
        self._mark_demo_lost(_('Demostración cancelada desde CRM.'))
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _check_can_create_quotation(self):
        if self.filtered(lambda lead: not lead.active and lead.probability == 0):
            raise UserError(_('No puedes generar una cotización para una oportunidad perdida. Reactiva la oportunidad antes de continuar.'))
        if self.filtered(lambda lead: (lead.demo_meeting_scheduled or lead.approval_request_id) and not lead.demo_end):
            raise UserError(_('No puedes generar una cotización mientras la demostración esté en curso. Marca "Demostración finalizada" antes de continuar.'))

    def action_sale_quotations_new(self):
        self._check_can_create_quotation()
        return super().action_sale_quotations_new()

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

    demo_approval_ids = fields.One2many('approval.request', 'crm_lead_id', string='Historial de aprobaciones')
    demo_approval_count = fields.Integer(compute='_compute_demo_approval_count')

    def _get_demo_approvals(self):
        self.ensure_one()
        requests = self.demo_approval_ids | self.approval_request_id
        if not self.env.su and self.env.user.has_group('entintados_pdv.group_demo_user'):
            return requests.search([('id', 'in', requests.ids)])
        return requests

    @api.depends('demo_approval_ids', 'approval_request_id')
    def _compute_demo_approval_count(self):
        for lead in self:
            lead.demo_approval_count = len(lead._get_demo_approvals())

    def _get_demo_pickings(self):
        self.ensure_one()
        requests = self._get_demo_approvals()
        return self.picking_ids | requests.picking_ids | requests.generated_picking_id

    def _has_material_output(self):
        self.ensure_one()
        return bool(self._get_demo_pickings().filtered(lambda picking: picking.state != 'cancel'))

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
    
    def _check_demo_before_leaving(self, old_stage, new_stage):
        """Valida que no se avance de la etapa de demo mientras haya una
        solicitud de materiales en curso sin que la demostración esté finalizada."""
        if old_stage.stage_type != 'demo':
            return
        if self.approval_request_id and not self.demo_end:
            raise ValidationError(_(
                'No puedes avanzar desde la etapa "%s" mientras la solicitud de '
                'salida de material esté en curso. Debes marcar "Demostración '
                'finalizada" antes de continuar.'
            ) % old_stage.name)

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
                record._check_demo_meeting_before_leaving(old_stage, new_stage)
                record._check_demo_before_leaving(old_stage, new_stage)

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
        if self._has_material_output():
            raise UserError(_("Ya existe una salida de inventario generada para esta oportunidad."))

    crm_meeting_ids = fields.One2many(
        'calendar.event', 'crm_lead_id', string="Reuniones CRM",
    )

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
    
    wharehouse_id = fields.Many2one(
        'stock.location',
        string="Almacén",
    )
    
    demo_end = fields.Boolean(
        string="Demostración finalizada",
    )

    @api.depends(
        'crm_meeting_ids.crm_activity_type_id',
        'crm_meeting_ids.active',
        'crm_meeting_ids.start',
        'crm_meeting_ids.stop',
        'crm_meeting_ids.allday',
    )
    def _compute_meeting_scheduled(self):
        
        demo_type = self.env.ref(DEMO_ACTIVITY_XMLID, raise_if_not_found=False)
        visit_type = self.env.ref(FIELD_VISIT_ACTIVITY_XMLID, raise_if_not_found=False)
        for lead in self:
            lead.demo_meeting_scheduled = lead._is_meeting_scheduled(demo_type)
            lead.field_visit_scheduled = lead._is_meeting_scheduled(visit_type)

    def _get_meeting_activities(self, activity_type):
        """Actividades (de reunión) del tipo dado en la oportunidad (recordset)."""
        self.ensure_one()
        if not activity_type:
            return self.env['mail.activity']
        return self.activity_ids.filtered(
            lambda a: a.activity_type_id == activity_type
        )

    def _is_meeting_scheduled(self, activity_type):
        """True si la oportunidad tiene una reunión (activa y con horario) de ese tipo.

        Se apoya en el evento de calendario etiquetado (no en la actividad) para
        que el estado sobreviva aunque la actividad se marque como hecha, ya que
        Odoo elimina el mail.activity al completarlo.
        """
        self.ensure_one()
        if not activity_type:
            return False
        return bool(self.env['calendar.event'].sudo().search_count([
            ('crm_lead_id', '=', self.id),
            ('crm_activity_type_id', '=', activity_type.id),
            ('allday', '=', False),
            ('start', '!=', False),
            ('stop', '!=', False),
        ]))

    def _create_meeting_activity(self, activity_type):
        """Crea una nueva actividad de reunión del tipo indicado."""
        self.ensure_one()
        activity = self.activity_schedule(
            activity_type_id=activity_type.id,
            user_id=self.user_id.id or self.env.uid,
            summary=activity_type.summary,
        )
        # activity_schedule devuelve False si el contexto trae
        # 'mail_activity_automation_skip'.
        if not activity:
            raise UserError(_("No se pudo crear la actividad de reunión."))
        return activity

    def _get_or_create_meeting_activity(self, activity_type):
        """Devuelve la actividad de reunión existente o crea una si no hay ninguna."""
        self.ensure_one()
        if not activity_type:
            return self.env['mail.activity']
        activity = self._get_meeting_activities(activity_type)[:1]
        return activity or self._create_meeting_activity(activity_type)

    def _open_calendar_for_activity(self, activity, activity_type):
        """Abre el calendario para agendar (bloque de horario y campos) la actividad dada."""
        self.ensure_one()
        # Reutiliza el flujo nativo de Odoo (actividad de reunión -> calendario)
        # cuando está disponible; si no, abre el calendario con el evento enlazado.
        if hasattr(activity, 'action_create_calendar_event'):
            action = activity.action_create_calendar_event()
        else:
            action = self.env['ir.actions.act_window']._for_xml_id(
                'calendar.action_calendar_event'
            )
        context = action.get('context')
        context = dict(context) if isinstance(context, dict) else {}
        # Etiqueta el evento con la oportunidad y el tipo de reunión, para que el
        # estado de "agendada" persista aunque la actividad se marque como hecha.
        context.update({
            'default_crm_lead_id': self.id,
            'default_crm_activity_type_id': activity_type.id,
            'default_activity_ids': [(6, 0, activity.ids)],
            'default_res_model': 'crm.lead',
            'default_res_id': self.id,
            'default_name': activity.summary or activity_type.name,
            'default_user_id': self.user_id.id or self.env.uid,
        })
        action['context'] = context
        return action

    def _action_schedule_meeting(self, activity_xmlid):
        """Agenda la reunión indicada: reabre la existente activa o abre el calendario."""
        self.ensure_one()
        activity_type = self.env.ref(activity_xmlid, raise_if_not_found=False)
        if not activity_type:
            raise UserError(_(
                "No está configurado el tipo de actividad requerido. "
                "Verifica que el módulo esté correctamente instalado/actualizado."
            ))
        activity = self._get_or_create_meeting_activity(activity_type)
        event = activity.calendar_event_id
        if event and event.active:
            # Ya hay una reunión agendada: abre ese evento para consultarlo o
            # reagendarlo, en vez de crear uno nuevo (evita duplicados/huérfanos).
            return {
                'type': 'ir.actions.act_window',
                'name': activity_type.display_name,
                'res_model': 'calendar.event',
                'res_id': event.id,
                'view_mode': 'form',
                'target': 'new',
            }
        if event:
            # El evento anterior fue cancelado/archivado: se desvincula para
            # poder agendar uno nuevo desde cero.
            activity.calendar_event_id = False
        return self._open_calendar_for_activity(activity, activity_type)

    def action_schedule_demo_meeting(self):
        """Agenda la demostración en el calendario (requiere líneas de material)."""
        self.ensure_one()
        return self._action_schedule_meeting(DEMO_ACTIVITY_XMLID)

    def _create_field_visit_meeting(self):
        """Crea una nueva visita de campo y abre el calendario para agendarla."""
        self.ensure_one()
        activity_type = self.env.ref(FIELD_VISIT_ACTIVITY_XMLID, raise_if_not_found=False)
        if not activity_type:
            raise UserError(_(
                "No está configurado el tipo de actividad 'Visita de campo'. "
                "Verifica que el módulo esté correctamente instalado/actualizado."
            ))
        activity = self._create_meeting_activity(activity_type)
        return self._open_calendar_for_activity(activity, activity_type)

    def action_schedule_field_visit(self):
        """Agenda una visita de campo; si ya existe una actividad, pide confirmación."""
        self.ensure_one()
        activity_type = self.env.ref(FIELD_VISIT_ACTIVITY_XMLID, raise_if_not_found=False)
        if activity_type and self._get_meeting_activities(activity_type):
            return {
                'type': 'ir.actions.act_window',
                'name': _('Visita de campo ya registrada'),
                'res_model': 'crm.field.visit.confirm.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_lead_id': self.id},
            }
        return self._create_field_visit_meeting()

    def _check_field_visit_before_leaving(self, old_stage, new_stage):
        """Valida avance y retroceso en etapa de visita de campo."""
        if old_stage.stage_type != 'visit':
            return
        ordered_ids = self.env['crm.stage'].search([], order='sequence, id').ids
        if old_stage.id not in ordered_ids or new_stage.id not in ordered_ids:
            return

        old_index = ordered_ids.index(old_stage.id)
        new_index = ordered_ids.index(new_stage.id)
        visit_type = self.env.ref(FIELD_VISIT_ACTIVITY_XMLID, raise_if_not_found=False)
        has_pending_activity = bool(visit_type and self._get_meeting_activities(visit_type))

        if new_index < old_index:
            if self.field_visit_scheduled or has_pending_activity:
                raise ValidationError(_(
                    'No puedes regresar a "%s" si la oportunidad ya tiene una visita de campo registrada.'
                ) % new_stage.name)
            return

        if not self.field_visit_scheduled:
            raise ValidationError(_(
                'Antes de avanzar desde la etapa "%s" debes agendar la visita de '
                'campo en el calendario con horario de inicio y fin.'
            ) % old_stage.name)
        if has_pending_activity:
            raise ValidationError(_(
                'Antes de avanzar desde la etapa "%s" debes marcar como realizada '
                '(hecha) la actividad de Visita de campo.'
            ) % old_stage.name)
            
    def _check_demo_meeting_before_leaving(self, old_stage, new_stage):
        """Valida avance y retroceso en etapa de demo (reunión de demostración)."""
        if old_stage.stage_type != 'demo':
            return
        ordered_ids = self.env['crm.stage'].search([], order='sequence, id').ids
        if old_stage.id not in ordered_ids or new_stage.id not in ordered_ids:
            return

        old_index = ordered_ids.index(old_stage.id)
        new_index = ordered_ids.index(new_stage.id)
        demo_type = self.env.ref(DEMO_ACTIVITY_XMLID, raise_if_not_found=False)
        has_pending_activity = bool(demo_type and self._get_meeting_activities(demo_type))

        if new_index < old_index:
            if self.demo_meeting_scheduled or has_pending_activity:
                raise ValidationError(_(
                    'No puedes regresar a "%s" si la oportunidad ya tiene una '
                    'demostración registrada.'
                ) % new_stage.name)
            return

        if not self.demo_meeting_scheduled:
            raise ValidationError(_(
                'Antes de avanzar desde la etapa "%s" debes agendar la demostración '
                'en el calendario con horario de inicio y fin.'
            ) % old_stage.name)
        if has_pending_activity:
            raise ValidationError(_(
                'Antes de avanzar desde la etapa "%s" debes marcar como realizada '
                '(hecha) la actividad de Demostración.'
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
        self.ensure_one()
        self._ensure_material_request_allowed()
        if not self.demo_meeting_scheduled:
            raise UserError(_(
                "Primero agenda la demostración en el calendario "
                "(marca el horario de inicio y fin del evento)."
            ))
        if self.approval_request_id:
            raise UserError(_("Ya existe una solicitud de aprobación en curso para esta oportunidad."))
        
        if not self.partner_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'crm.material.request.partner.wizzard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_lead_id': self.id},
            }
            
        if not self.wharehouse_id:
            raise UserError(_("Debes seleccionar un almacén de origen para la salida de material."))

        # Revisar de nuevo: el inventario puede cambiar después de capturar las líneas.
        self.material_line_ids._check_material_stock()

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

        approvers = self._get_material_approvers()

        # The seller can request a demonstration without editing approvals.
        # All input comes from this writable opportunity and the organigram.
        self.check_access('write')
        approval_request = self.env['approval.request'].sudo().create({
            'category_id': category.id,
            'request_owner_id': self.user_id.id or self.env.uid,
            'partner_id': self.partner_id.id,
            'warehouse_id': self.wharehouse_id.id,
            'date': fields.Date.context_today(self),
            'crm_lead_id': self.id,
            'reason': '\n'.join(
                '%s x %s %s' % (line.quantity, line.uom_id.name or '', line.product_id.display_name)
                for line in self.material_line_ids
            ),
            'approver_ids': [(0, 0, {'user_id': user.id, 'required': False}) for user in approvers],
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

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'crm.lead',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'current',
        }   
        
    picking_ids = fields.One2many(
        comodel_name="stock.picking",
        inverse_name="crm_lead_id",
        string="Salidas",
    )

    picking_count = fields.Integer(
        string="Número de salidas",
        compute="_compute_picking_count",
    )

    @api.depends('picking_ids', 'demo_approval_ids.picking_ids', 'demo_approval_ids.generated_picking_id',
                 'approval_request_id.picking_ids', 'approval_request_id.generated_picking_id')
    def _compute_picking_count(self):
        for lead in self:
            lead.picking_count = len(lead._get_demo_pickings())

    def action_view_pickings(self):
        self.ensure_one()
        pickings = self._get_demo_pickings()

        action = {
            "type": "ir.actions.act_window",
            "name": "Salidas",
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [
                ("id", "in", pickings.ids),
            ],
            "context": {
                "default_crm_lead_id": self.id,
                "default_partner_id": self.partner_id.id,
            },
        }

        # Si solamente existe una salida,
        # abrirla directamente en formulario.
        if len(pickings) == 1:
            action.update({
                "view_mode": "form",
                "res_id": pickings.id,
            })

        return action

    def _get_material_report_values(self):
        self.ensure_one()
        picking = self.picking_ids.filtered(
            lambda item: item._is_material_output_type() and item.state != 'cancel'
        ).sorted('id', reverse=True)[:1]
        approver = picking.material_auditor_id or self._get_material_approver()
        return {
            'lead': self,
            'picking': picking,
            'partner': self.partner_id,
            'salesperson': self.user_id or self.create_uid,
            'approver': approver,
            # CRM's template uses quantity/uom_id/description, not stock.move fields.
            'lines': self.material_line_ids,
            'date': fields.Date.context_today(self),
            'approval_state': picking.material_approval_state if picking else 'to_audit',
            'signature': picking.signature if picking else self._get_user_digital_signature(approver),
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
        return False
    
    def _get_material_approver(self):
        self.ensure_one()
        if not self.user_id:
            return self.env['res.users']
        employee = self.env['hr.employee'].sudo().search([
            ('user_id', '=', self.user_id.id),
            ('company_id', '=', (self.company_id or self.env.company).id),
        ], limit=1)
        if employee and employee.parent_id and employee.parent_id.user_id:
            return employee.parent_id.user_id
        return self.env['res.users']

    def _get_material_approvers(self):
        """Jefe del vendedor y usuarios de los dos departamentos comerciales, sin duplicados."""
        self.ensure_one()
        department_ids = []
        for xmlid in ('entintados_pdv.hr_department_direccion_comercial',
                      'entintados_pdv.hr_department_inteligencia_comercial'):
            department = self.env.ref(xmlid, raise_if_not_found=False)
            if department:
                department_ids.append(department.id)
        company = self.company_id or self.env.company
        employees = self.env['hr.employee'].sudo().search([
            ('department_id', 'in', department_ids), ('active', '=', True),
            ('user_id.active', '=', True), ('user_id.share', '=', False),
            ('user_id.company_ids', 'in', [company.id]),
        ])
        return (self._get_material_approver() | employees.user_id).filtered(
            lambda user: user.active and not user.share)
    
    def action_view_approval_request(self):
        self.ensure_one()
        requests = self._get_demo_approvals()
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Aprobaciones de la demostración'),
            'res_model': 'approval.request',
            'view_mode': 'list,form',
            'domain': [('id', 'in', requests.ids)],
            'target': 'current',
        }
        if len(requests) == 1:
            action.update({'view_mode': 'form', 'res_id': requests.id})
        return action

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
                    ) % (record._fields['stage_type'].convert_to_export(record.stage_type, record), duplicate.name))
