from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCrmOpportunityList(TransactionCase):
    def test_reference_on_create_and_copy(self):
        lead = self.env['crm.lead'].create({'name': 'Folio test', 'type': 'opportunity'})
        duplicate = lead.copy()
        self.assertRegex(lead.opportunity_reference, r'^OP-\d{4,}$')
        self.assertRegex(duplicate.opportunity_reference, r'^OP-\d{4,}$')
        self.assertNotEqual(lead.opportunity_reference, duplicate.opportunity_reference)

    def test_convert_preserves_reference(self):
        lead = self.env['crm.lead'].create({'name': 'Prospecto folio', 'type': 'lead'})
        self.assertFalse(lead.opportunity_reference)
        lead.write({'type': 'opportunity'})
        reference = lead.opportunity_reference
        self.assertTrue(reference)
        lead._assign_opportunity_references()
        lead.write({'name': 'Oportunidad renombrada', 'type': 'opportunity'})
        self.assertEqual(lead.opportunity_reference, reference)

    def test_relations_are_scoped_to_opportunity(self):
        lead, other = self.env['crm.lead'].create([
            {'name': 'Relaciones', 'type': 'opportunity'},
            {'name': 'Otra oportunidad', 'type': 'opportunity'},
        ])
        partner = self.env['res.partner'].create({'name': 'Cliente relaciones', 'phone': '5551234567'})
        quotation = self.env['sale.order'].create({'partner_id': partner.id, 'opportunity_id': lead.id})
        self.env['sale.order'].create({'partner_id': partner.id, 'opportunity_id': other.id})
        activity = self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get_id('crm.lead'),
            'res_id': lead.id, 'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'user_id': self.env.uid,
        })
        self.assertEqual(lead.opportunity_relations['quotations'], 1)
        self.assertEqual(lead.opportunity_relations['activities'], 1)
        self.assertEqual(lead.action_open_opportunity_relation('quotations')['res_id'], quotation.id)
        self.assertEqual(lead.action_open_opportunity_relation('activities')['res_id'], activity.id)
        self.assertEqual(lead.action_open_opportunity_relation('quotations')['views'], [(False, 'form')])
        self.assertEqual(lead.action_open_opportunity_relation('activities')['views'], [(False, 'form')])
        self.env['sale.order'].create({'partner_id': partner.id, 'opportunity_id': lead.id})
        action = lead.action_open_opportunity_relation('quotations')
        self.assertEqual(action['views'], [(False, 'list'), (False, 'form')])
        self.assertNotIn('res_id', action)
        with self.assertRaises(UserError):
            lead.action_open_opportunity_relation('unknown')
