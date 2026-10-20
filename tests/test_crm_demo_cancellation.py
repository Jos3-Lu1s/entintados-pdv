from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCrmDemoCancellation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage = cls.env['crm.stage'].search([('stage_type', '=', 'demo')], limit=1)
        if not cls.stage:
            cls.stage = cls.env['crm.stage'].create({'name': 'Demo cancellation test', 'stage_type': 'demo'})
        cls.category = cls.env.ref('entintados_pdv.approval_category_salida_material')
        cls.material_type = cls.env.ref('entintados_pdv.picking_type_material_output')

    def _lead(self):
        return self.env['crm.lead'].create({'name': 'Demo cancellation opportunity', 'type': 'opportunity',
                                           'stage_id': self.stage.id, 'probability': 60})

    def _request(self, lead=None, **extra):
        return self.env['approval.request'].create({
            'name': 'Ordinary approval', 'category_id': self.category.id,
            'request_owner_id': self.env.uid, 'crm_lead_id': lead.id if lead else False,
            'approver_ids': [Command.create({'user_id': self.env.uid})], **extra,
        })

    def _picking(self, lead, request, material=True):
        return self.env['stock.picking'].create({
            'picking_type_id': (self.material_type if material else self.env.ref('stock.picking_type_out')).id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            'crm_lead_id': lead.id if lead else False, 'approval_request_id': request.id if request else False,
        })

    def _assert_lost(self, lead):
        self.assertFalse(lead.active)
        self.assertEqual(lead.probability, 0)

    def test_sequence_only_for_crm_requests(self):
        lead = self._lead()
        first = self._request(lead)
        ordinary = self._request()
        second = self._request(lead)
        self.assertRegex(first.name, r'^APROV-\d{4}$')
        self.assertEqual(ordinary.name, 'Ordinary approval')
        self.assertEqual(int(second.name[6:]), int(first.name[6:]) + 1)

    def test_sequence_with_context_and_no_name(self):
        request = self.env['approval.request'].with_context(default_crm_lead_id=self._lead().id).create({
            'category_id': self.category.id, 'request_owner_id': self.env.uid,
        })
        self.assertRegex(request.name, r'^APROV-\d{4}$')

    def test_crm_sequence_takes_priority_without_changing_other_categories(self):
        category = self.env['approval.category'].create({
            'name': 'Other numbering', 'automated_sequence': True, 'sequence_code': 'OTHER',
        })
        crm = self._request(self._lead(), category_id=category.id)
        ordinary = self._request(category_id=category.id)
        self.assertRegex(crm.name, r'^APROV-\d{4}$')
        self.assertFalse(ordinary.name.startswith('APROV-'))
        self.assertTrue(category.automated_sequence)

    def test_cancel_approval_marks_only_its_opportunity_lost(self):
        lead, other = self._lead(), self._lead()
        request = self._request(lead)
        request.action_cancel()
        self.assertEqual(request.request_status, 'cancel')
        self._assert_lost(lead)
        self.assertTrue(other.active)
        request.action_cancel()
        self._assert_lost(lead)

    def test_ordinary_approval_does_not_change_crm(self):
        lead = self._lead()
        self._request().action_cancel()
        self.assertTrue(lead.active)
        self.assertEqual(lead.probability, 60)

    def test_cancel_material_output_marks_opportunity_lost(self):
        lead = self._lead()
        picking = self._picking(lead, self._request(lead))
        picking.action_cancel()
        self.assertEqual(picking.state, 'cancel')
        self._assert_lost(lead)

    def test_output_finds_opportunity_through_approval(self):
        lead = self._lead()
        picking = self._picking(None, self._request(lead))
        picking.action_cancel()
        self._assert_lost(lead)

    def test_unrelated_delivery_does_not_mark_opportunity_lost(self):
        lead = self._lead()
        self._picking(lead, None, material=False).action_cancel()
        self.assertTrue(lead.active)

    def test_cancel_from_crm_cancels_pending_documents(self):
        lead = self._lead()
        request = self._request(lead)
        lead.approval_request_id = request
        picking = self._picking(lead, request)
        lead.action_cancel_demo()
        self.assertEqual(request.request_status, 'cancel')
        self.assertEqual(picking.state, 'cancel')
        self._assert_lost(lead)

    def test_completed_demo_cannot_be_cancelled_from_crm(self):
        lead = self._lead()
        lead.demo_end = True
        with self.assertRaises(UserError):
            lead.action_cancel_demo()
        self.assertTrue(lead.active)

    def test_views_warn_before_demo_cancellation(self):
        for xmlid in ('entintados_pdv.crm_lead_view_form_demo_cancellation',
                      'entintados_pdv.approval_request_view_form_crm_cancellation',
                      'entintados_pdv.stock_picking_view_form_crm_demo_cancellation'):
            view = self.env.ref(xmlid)
            arch = view._get_combined_arch()
            buttons = arch.xpath('//button[@confirm]')
            self.assertTrue(any('perdida' in button.get('confirm', '') for button in buttons), xmlid)
