from datetime import datetime, time, timedelta

from odoo import api, fields, models, _

from odoo.exceptions import UserError

from ast import literal_eval


DEMO_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_demo'
APPROVAL_CATEGORY_XMLID = 'entintados_pdv.approval_category_salida_material'

FIELD_VISIT_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_field_visit'

ACTIVIDAD_ETAPA_REQUERIDA = {
    DEMO_ACTIVITY_XMLID:        'demo',
    FIELD_VISIT_ACTIVITY_XMLID: 'visit',
}

ACTIVIDADES_UNICAS = (DEMO_ACTIVITY_XMLID,)

class MailActivity(models.Model):
    """Soporte de calendario (reuniones con hora y eventos de día completo) y acceso de lectura ampliado."""
    _inherit = 'mail.activity'

    # --- Campos para vista de calendario -------------------------------------
    activity_date_start = fields.Datetime(
        string="Inicio (calendario)",
        compute="_compute_calendar_slot",
        inverse="_inverse_calendar_slot",
        store=True,
        help="Fecha y hora de inicio para la vista de calendario.",
    )
    activity_date_stop = fields.Datetime(
        string="Fin (calendario)",
        compute="_compute_calendar_slot",
        inverse="_inverse_calendar_slot",
        store=True,
        help="Fecha y hora de fin para la vista de calendario.",
    )
    activity_all_day = fields.Boolean(
        string="Todo el día (calendario)",
        compute="_compute_calendar_slot",
        store=True,
        help="Indica si la actividad se muestra como evento de día completo.",
    )

    activity_involved_user_ids = fields.Many2many(
        "res.users",
        "mail_activity_involved_users_rel",
        "activity_id",
        "user_id",
        string="Involucrados",
        compute="_compute_involved_user_ids",
        store=True,
        help="Usuarios involucrados en la actividad (responsable y asistentes internos de la reunión).",
    )

    @api.depends(
        "date_deadline",
        "calendar_event_id",
        "calendar_event_id.start",
        "calendar_event_id.stop",
        "calendar_event_id.allday",
    )
    def _compute_calendar_slot(self):
        for activity in self:
            event = activity.calendar_event_id
            timed_meeting = bool(event and not event.allday and event.start and event.stop)
            if timed_meeting:
                # Reunión con horario definido
                activity.activity_all_day = False
                activity.activity_date_start = event.start
                activity.activity_date_stop = event.stop
            else:
                # Actividad de día completo (fecha del evento o fecha límite)
                base_date = False
                if event and event.start:
                    base_date = event.start.date()
                elif activity.date_deadline:
                    base_date = activity.date_deadline
                activity.activity_all_day = True
                if base_date:
                    # 12:00 UTC para evitar desfases por zona horaria
                    slot = datetime.combine(base_date, time(12, 0))
                    activity.activity_date_start = slot
                    activity.activity_date_stop = slot
                else:
                    activity.activity_date_start = False
                    activity.activity_date_stop = False

    def _inverse_calendar_slot(self):
        """Actualiza fechas en el evento o la actividad al moverla en el calendario."""
        for activity in self:
            start = activity.activity_date_start
            if not start:
                continue
            event = activity.calendar_event_id
            if not event:
                new_deadline = start.date()
                if new_deadline != activity.date_deadline:
                    activity.date_deadline = new_deadline
                continue
            if event.allday:
                # Evento de día completo: actualiza fechas manteniendo la duración en días
                new_start_date = start.date()
                duration_days = 0
                if event.start_date and event.stop_date:
                    duration_days = (event.stop_date - event.start_date).days
                new_stop_date = new_start_date + timedelta(days=duration_days)
                if event.start_date != new_start_date or event.stop_date != new_stop_date:
                    event.write({
                        "start_date": new_start_date,
                        "stop_date": new_stop_date,
                    })
            else:
                # Evento con horario: actualiza inicio y fin
                vals = {}
                if event.start != start:
                    vals["start"] = start
                if activity.activity_date_stop and event.stop != activity.activity_date_stop:
                    vals["stop"] = activity.activity_date_stop
                if vals:
                    event.write(vals)

    @api.depends("user_id", "calendar_event_id", "calendar_event_id.partner_ids")
    def _compute_involved_user_ids(self):
        """Calcula usuarios internos involucrados (responsable y asistentes del evento)."""
        all_partner_ids = set()
        for activity in self:
            if activity.calendar_event_id:
                all_partner_ids.update(activity.calendar_event_id.partner_ids.ids)

        partner_to_users = {}
        if all_partner_ids:
            internal_users = self.env["res.users"].search([
                ("partner_id", "in", list(all_partner_ids)),
                ("share", "=", False),
            ])
            for user in internal_users:
                partner_to_users.setdefault(user.partner_id.id, self.env["res.users"])
                partner_to_users[user.partner_id.id] |= user

        for activity in self:
            users = activity.user_id
            event = activity.calendar_event_id
            if event:
                for partner_id in event.partner_ids.ids:
                    users |= partner_to_users.get(partner_id, self.env["res.users"])
            activity.activity_involved_user_ids = users

    # --- Permisos de administrador de actividades ----------------------------
    def _is_activity_analyst(self):
        """Verifica si el usuario pertenece al grupo de administradores de actividades."""
        return self.env.user.has_group('entintados_pdv.group_activity_manager')

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, *, bypass_access=False, **kwargs):
        # Omite restricciones de acceso para administradores de actividades
        if not bypass_access and self._is_activity_analyst():
            bypass_access = True
        return super()._search(domain, offset, limit, order, bypass_access=bypass_access, **kwargs)

    def _check_access(self, operation):
        # Permite lectura global al administrador de actividades
        if operation == 'read' and self._is_activity_analyst():
            return None
        return super()._check_access(operation)
    
    def action_feedback(self, feedback=False, attachment_ids=None):
        demo_activity_type = self.env.ref(DEMO_ACTIVITY_XMLID, raise_if_not_found=False)
        category_salida = self.env.ref(APPROVAL_CATEGORY_XMLID, raise_if_not_found=False)
    
        for activity in self:
            # --- Bloqueo de la actividad de Demostración ---
            if (
                demo_activity_type
                and activity.activity_type_id == demo_activity_type
                and activity.res_model == 'crm.lead'
            ):
                lead = self.env['crm.lead'].browse(activity.res_id)
                if not lead.demo_end:
                    raise UserError(_(
                        "No puedes marcar esta actividad como hecha manualmente. "
                        "Se completa automáticamente al validar el uso de material "
                        "(adjuntando evidencia y validando la entrega)."
                    ))
    
            # --- Redirección: aprobar salida de material desde la actividad ---
            if activity.res_model == 'approval.request' and category_salida:
                request = self.env['approval.request'].browse(activity.res_id)
                if request.category_id == category_salida and not request.material_auditor_ids:
                    return request.action_approve()
    
        return super().action_feedback(feedback=feedback, attachment_ids=attachment_ids)
    
    # --- Actividades permitidas solo en ciertas etapas del CRM ---------------
    @api.model
    def _reglas_actividad_etapa(self):
        """{id del tipo de actividad: stage_type requerido}"""
        reglas = {}
        for xmlid, stage_type in ACTIVIDAD_ETAPA_REQUERIDA.items():
            tipo = self.env.ref(xmlid, raise_if_not_found=False)
            if tipo:
                reglas[tipo.id] = stage_type
        return reglas

    @api.model
    def _mensaje_actividad_fuera_de_etapa(self, activity_type, lead, stage_type):
        etapa = self.env['crm.stage'].search([('stage_type', '=', stage_type)], limit=1)
        selection = self.env['crm.stage']._fields['stage_type'].selection or []
        if isinstance(selection, (list, tuple)):
            etiquetas = dict(selection)
        else:
            etiquetas = {}
        return _(
            'La actividad "%(actividad)s" solo se puede programar cuando la oportunidad '
            'está en la etapa "%(etapa)s".\n\n'
            'La oportunidad "%(lead)s" está actualmente en "%(actual)s".'
        ) % {
            'actividad': activity_type.name,
            'etapa':     etapa.name or etiquetas.get(stage_type, stage_type),
            'lead':      lead.name,
            'actual':    lead.stage_id.name or _('sin etapa'),
        }

    @api.model
    def _validar_lead_para_tipo(self, lead, activity_type):
        """Lanza error si el tipo de actividad no está permitido en la etapa actual del lead."""
        stage_type = self._reglas_actividad_etapa().get(activity_type.id)
        if stage_type and lead.stage_id.stage_type != stage_type:
            raise UserError(self._mensaje_actividad_fuera_de_etapa(activity_type, lead, stage_type))

    def _check_actividad_en_etapa(self):
        if self.env.context.get('skip_activity_stage_check'):
            return
        reglas = self._reglas_actividad_etapa()
        if not reglas:
            return
        for act in self:
            if act.activity_type_id.id not in reglas or act.res_model != 'crm.lead' or not act.res_id:
                continue
            lead = self.env['crm.lead'].browse(act.res_id).exists()
            if lead:
                self._validar_lead_para_tipo(lead, act.activity_type_id)
                
    @api.model
    def _lead_ya_tiene_actividad_unica(self, lead, activity_type, excluir_id=False):
        """True si la oportunidad ya tiene esa actividad (pendiente, agendada o finalizada)."""
        domain = [
            ('res_model', '=', 'crm.lead'),
            ('res_id', '=', lead.id),
            ('activity_type_id', '=', activity_type.id),
        ]
        if excluir_id:
            domain.append(('id', '!=', excluir_id))
        if self.sudo().search_count(domain):
            return True
        if lead._is_meeting_scheduled(activity_type):
            return True
        demo = self.env.ref(DEMO_ACTIVITY_XMLID, raise_if_not_found=False)
        return bool(demo and activity_type == demo and lead.demo_end)

    @api.model
    def _mensaje_actividad_duplicada(self, activity_type, lead):
        return _(
            'La oportunidad "%(lead)s" ya tiene una actividad de "%(tipo)s". '
            'No se puede crear otra.\n\n'
            'Usa la que ya existe o el botón "Agendar demostración" para consultarla '
            'o reagendarla.'
        ) % {'lead': lead.name, 'tipo': activity_type.name}

    def _check_actividad_unica(self):
        if self.env.context.get('skip_unique_activity_check'):
            return
        for xmlid in ACTIVIDADES_UNICAS:
            tipo = self.env.ref(xmlid, raise_if_not_found=False)
            if not tipo:
                continue
            for act in self.filtered(lambda a: a.activity_type_id == tipo
                                     and a.res_model == 'crm.lead' and a.res_id):
                lead = self.env['crm.lead'].browse(act.res_id).exists()
                if lead and self._lead_ya_tiene_actividad_unica(lead, tipo, excluir_id=act.id):
                    raise UserError(self._mensaje_actividad_duplicada(tipo, lead))

    @api.model_create_multi
    def create(self, vals_list):
        activities = super().create(vals_list)
        activities._check_actividad_en_etapa()
        activities._check_actividad_unica()
        return activities

    def write(self, vals):
        res = super().write(vals)
        if {'activity_type_id', 'res_id', 'res_model_id'} & set(vals):
            self._check_actividad_en_etapa()
            self._check_actividad_unica()
        return res
class MailActivitySchedule(models.TransientModel):
    _inherit = 'mail.activity.schedule'

    @api.onchange('activity_type_id')
    def _onchange_actividad_fuera_de_etapa(self):
        if not self.activity_type_id or self.res_model != 'crm.lead':
            return

        try:
            ids = literal_eval(self.res_ids) if self.res_ids else []
        except (ValueError, SyntaxError):
            return
        leads = self.env['crm.lead'].browse(ids).exists()
        if not leads:
            return

        Activity = self.env['mail.activity']
        tipo = self.activity_type_id

        # 1. Etapa incorrecta
        stage_type = Activity._reglas_actividad_etapa().get(tipo.id)
        if stage_type:
            fuera = leads.filtered(lambda l: l.stage_id.stage_type != stage_type)
            if fuera:
                return {'warning': {
                    'title': _('Etapa incorrecta para esta actividad'),
                    'message': Activity._mensaje_actividad_fuera_de_etapa(tipo, fuera[0], stage_type),
                }}

        # 2. Actividad que solo puede existir una vez
        unicos = [self.env.ref(x, raise_if_not_found=False) for x in ACTIVIDADES_UNICAS]
        if tipo in [u for u in unicos if u]:
            repetidos = leads.filtered(lambda l: Activity._lead_ya_tiene_actividad_unica(l, tipo))
            if repetidos:
                return {'warning': {
                    'title': _('Actividad ya registrada'),
                    'message': Activity._mensaje_actividad_duplicada(tipo, repetidos[0]),
                }}