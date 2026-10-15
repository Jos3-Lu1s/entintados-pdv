# -*- coding: utf-8 -*-
from odoo import fields, models, api, _

DEMO_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_demo'
FIELD_VISIT_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_field_visit'

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
        campos_enlace = {'res_model', 'res_model_id', 'res_id', 'activity_ids'}
        if not self.env.context.get('skip_crm_meeting_sync') and campos_enlace & set(vals):
            self._sync_crm_meeting_tags()
        return res

    def _sync_crm_meeting_tags(self):
        """Pone crm_lead_id y crm_activity_type_id a los eventos ligados a una
        oportunidad, aunque se hayan creado desde el botón nativo de Actividades."""
        tipos = self.env['mail.activity.type']
        for xmlid in (DEMO_ACTIVITY_XMLID, FIELD_VISIT_ACTIVITY_XMLID):
            tipo = self.env.ref(xmlid, raise_if_not_found=False)
            if tipo:
                tipos |= tipo
        if not tipos:
            return

        tipo_contexto = self.env.context.get('default_activity_type_id')

        for event in self:
            if event.res_model != 'crm.lead' or not event.res_id:
                continue

            vals = {}
            if not event.crm_lead_id:
                vals['crm_lead_id'] = event.res_id

            if not event.crm_activity_type_id:
                tipo = event.activity_ids.activity_type_id.filtered(lambda t: t in tipos)[:1]
                if not tipo and tipo_contexto in tipos.ids:
                    tipo = tipos.filtered(lambda t: t.id == tipo_contexto)
                if tipo:
                    vals['crm_activity_type_id'] = tipo.id

            if vals:
                event.sudo().with_context(skip_crm_meeting_sync=True).write(vals)
