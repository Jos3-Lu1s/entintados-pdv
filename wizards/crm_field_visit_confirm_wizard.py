# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmFieldVisitConfirmWizard(models.TransientModel):
    """Confirma el registro de otra visita de campo cuando ya existe una."""
    _name = "crm.field.visit.confirm.wizard"
    _description = "Confirmar registro de otra visita de campo"

    lead_id = fields.Many2one(
        "crm.lead",
        string="Oportunidad",
        required=True,
        ondelete="cascade",
    )

    def action_confirm(self):
        """Registra otra visita de campo y abre el calendario para agendarla."""
        self.ensure_one()
        return self.lead_id._create_field_visit_meeting()
