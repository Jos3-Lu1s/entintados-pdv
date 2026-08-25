# -*- coding: utf-8 -*-

from datetime import datetime, time, timedelta

from odoo import api, fields, models


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
