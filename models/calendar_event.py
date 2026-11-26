# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import api, fields, models

# Campos que enlazan el evento a un registro; si cambian, se re-etiqueta.
CAMPOS_ENLACE = frozenset({'res_model', 'res_model_id', 'res_id', 'activity_ids'})


class CalendarEvent(models.Model):
    """Etiqueta las reuniones CRM (demostración, visita de campo) para que su
    estado de 'agendada' persista aunque la actividad de origen se marque como
    hecha (Odoo elimina el mail.activity al completarlo)."""
    _inherit = 'calendar.event'

    crm_lead_id = fields.Many2one(
        'crm.lead',
        string="Oportunidad CRM",
        index=True,
        ondelete='cascade',
        help="Oportunidad a la que pertenece esta reunión CRM.",
    )
    crm_activity_type_id = fields.Many2one(
        'mail.activity.type',
        string="Tipo de reunión CRM",
        index=True,
        help="Tipo de reunión CRM que originó este evento "
             "(demostración, visita de campo).",
    )

    @api.model_create_multi
    def create(self, vals_list):
        events = super().create(vals_list)
        events._sync_crm_meeting_tags()
        return events

    def write(self, vals):
        res = super().write(vals)
        if CAMPOS_ENLACE & set(vals):
            self._sync_crm_meeting_tags()
        return res

    def _sync_crm_meeting_tags(self):
        """Pone crm_lead_id y crm_activity_type_id a los eventos ligados a una
        oportunidad, aunque se hayan creado desde el botón nativo de Actividades."""
        pendientes = self.filtered(
            lambda e: e.res_model == 'crm.lead' and e.res_id
            and not (e.crm_lead_id and e.crm_activity_type_id)
        )
        if not pendientes:
            return

        tipos = self.env['mail.activity.type'].browse(
            list(self.env['mail.activity']._reglas_actividad_etapa())
        )
        if not tipos:
            return
        tipo_contexto = tipos & tipos.browse(self.env.context.get('default_activity_type_id') or [])

        # Agrupa los eventos con los mismos valores para escribir una vez por grupo.
        grupos = defaultdict(lambda: self.browse())
        for event in pendientes:
            vals = {}
            if not event.crm_lead_id:
                vals['crm_lead_id'] = event.res_id
            if not event.crm_activity_type_id:
                tipo = (event.activity_ids.activity_type_id & tipos)[:1] or tipo_contexto
                if tipo:
                    vals['crm_activity_type_id'] = tipo.id
            if vals:
                grupos[tuple(sorted(vals.items()))] |= event

        # Los campos escritos no están en CAMPOS_ENLACE, así que no hay recursión.
        for vals, events in grupos.items():
            events.sudo().write(dict(vals))
