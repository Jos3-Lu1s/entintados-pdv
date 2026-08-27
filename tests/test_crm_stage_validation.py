# -*- coding: utf-8 -*-

from datetime import datetime, timedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCrmStageValidation(TransactionCase):
    """Casos de prueba para validaciones de transición de etapas CRM."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.visit_activity_type = cls.env.ref('entintados_pdv.mail_activity_type_field_visit')

        # Buscar o crear etapas en orden secuencial
        cls.stage_new = cls.env['crm.stage'].search([('stage_type', '=', 'new')], limit=1)
        if not cls.stage_new:
            cls.stage_new = cls.env['crm.stage'].create({'name': 'Prospecto', 'stage_type': 'new', 'sequence': 1})

        cls.stage_visit = cls.env['crm.stage'].search([('stage_type', '=', 'visit')], limit=1)
        if not cls.stage_visit:
            cls.stage_visit = cls.env['crm.stage'].create({'name': 'Visita de campo', 'stage_type': 'visit', 'sequence': 2})

        cls.stage_demo = cls.env['crm.stage'].search([('stage_type', '=', 'demo')], limit=1)
        if not cls.stage_demo:
            cls.stage_demo = cls.env['crm.stage'].create({'name': 'Demo', 'stage_type': 'demo', 'sequence': 3})

        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Prueba CRM',
            'phone': '5512345678',
        })

    def _create_lead(self, stage):
        return self.env['crm.lead'].create({
            'name': 'Oportunidad Prueba',
            'partner_id': self.partner.id,
            'stage_id': stage.id,
        })

    def test_cannot_advance_without_field_visit_scheduled(self):
        """No permite avanzar de Visita a Demo si no hay reunión agendada en calendario."""
        lead = self._create_lead(self.stage_visit)
        self.assertFalse(lead.field_visit_scheduled)
        with self.assertRaises(ValidationError) as cm:
            lead.stage_id = self.stage_demo.id
        self.assertIn("agendar la visita de campo", str(cm.exception))

    def test_cannot_advance_with_pending_field_visit_activity(self):
        """No permite avanzar de Visita a Demo si la actividad de visita sigue abierta."""
        lead = self._create_lead(self.stage_visit)

        # Crear reunión en calendario (con horario)
        now = datetime.now()
        self.env['calendar.event'].create({
            'name': 'Visita agendada',
            'start': now + timedelta(days=1),
            'stop': now + timedelta(days=1, hours=1),
            'allday': False,
            'crm_lead_id': lead.id,
            'crm_activity_type_id': self.visit_activity_type.id,
        })
        # Crear actividad pendiente
        lead.activity_schedule(
            activity_type_id=self.visit_activity_type.id,
            summary='Visita de campo pendiente',
        )

        self.assertTrue(lead.field_visit_scheduled)
        self.assertTrue(bool(lead._get_meeting_activities(self.visit_activity_type)))

        with self.assertRaises(ValidationError) as cm:
            lead.stage_id = self.stage_demo.id
        self.assertIn("marcar como realizada", str(cm.exception))

    def test_advance_when_field_visit_done(self):
        """Permite avanzar a Demo una vez agendada y completada (hecha) la actividad."""
        lead = self._create_lead(self.stage_visit)

        now = datetime.now()
        self.env['calendar.event'].create({
            'name': 'Visita agendada',
            'start': now + timedelta(days=1),
            'stop': now + timedelta(days=1, hours=1),
            'allday': False,
            'crm_lead_id': lead.id,
            'crm_activity_type_id': self.visit_activity_type.id,
        })
        activity = lead.activity_schedule(
            activity_type_id=self.visit_activity_type.id,
            summary='Visita de campo',
        )

        # Marcar actividad como hecha (feedback)
        activity.action_feedback(feedback="Visita realizada exitosamente.")

        self.assertTrue(lead.field_visit_scheduled)
        self.assertFalse(bool(lead._get_meeting_activities(self.visit_activity_type)))

        # Debe permitir cambiar la etapa a Demo sin error
        lead.stage_id = self.stage_demo.id
        self.assertEqual(lead.stage_id.id, self.stage_demo.id)

    def test_can_return_to_prospect_without_field_visit(self):
        """Permite regresar de Visita a Prospecto si no hay visita agendada ni actividad abierta."""
        lead = self._create_lead(self.stage_visit)
        lead.stage_id = self.stage_new.id
        self.assertEqual(lead.stage_id.id, self.stage_new.id)

    def test_cannot_return_to_prospect_with_scheduled_visit(self):
        """No permite regresar de Visita a Prospecto si ya tiene visita agendada."""
        lead = self._create_lead(self.stage_visit)
        now = datetime.now()
        self.env['calendar.event'].create({
            'name': 'Visita agendada',
            'start': now + timedelta(days=1),
            'stop': now + timedelta(days=1, hours=1),
            'allday': False,
            'crm_lead_id': lead.id,
            'crm_activity_type_id': self.visit_activity_type.id,
        })
        with self.assertRaises(ValidationError) as cm:
            lead.stage_id = self.stage_new.id
        self.assertIn("ya tiene una visita de campo registrada", str(cm.exception))

    def test_cannot_return_to_prospect_with_pending_visit_activity(self):
        """No permite regresar de Visita a Prospecto si tiene una actividad de visita abierta."""
        lead = self._create_lead(self.stage_visit)
        lead.activity_schedule(
            activity_type_id=self.visit_activity_type.id,
            summary='Visita pendiente',
        )
        with self.assertRaises(ValidationError) as cm:
            lead.stage_id = self.stage_new.id
        self.assertIn("ya tiene una visita de campo registrada", str(cm.exception))
