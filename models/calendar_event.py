# -*- coding: utf-8 -*-
from odoo import fields, models


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
