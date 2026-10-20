from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCrmDemoCancellation(TransactionCase):
    def test_cancel_from_stock_closes_all_related_documents(self):
        lead = self._lead()
        request, another = self._request(lead), self._request(lead)
        lead.approval_request_id = request
        picking = self._picking(lead, request)
        sibling = self._picking(None, another)
        other_lead = self._lead()
        unrelated = self._request(other_lead)
        picking.action_cancel()
        self.assertEqual(request.request_status, 'cancel')
        self.assertEqual(another.request_status, 'cancel')
        self.assertEqual(sibling.state, 'cancel')
        self.assertNotEqual(unrelated.request_status, 'cancel')
        self._assert_lost(lead)
        picking.action_cancel()

    def test_cancel_from_approval_closes_related_output(self):
        lead = self._lead()
        request = self._request(lead)
        lead.approval_request_id = request
        picking = self._picking(None, request)
        request.action_cancel()
        self.assertEqual(picking.state, 'cancel')
        self._assert_lost(lead)

    def test_lost_opportunity_blocks_new_quotation(self):
        lead = self._lead()
        lead.action_set_lost()
        partner = self.env['res.partner'].create({'name': 'Lost opportunity customer'})
        with self.assertRaises(UserError):
            lead.action_sale_quotations_new()
        with self.assertRaises(UserError):
            self.env['sale.order'].create({'partner_id': partner.id, 'opportunity_id': lead.id})
        with self.assertRaises(UserError):
            self.env['sale.order'].with_context(default_opportunity_id=lead.id).create({'partner_id': partner.id})
        lead.write({'active': True})
        lead._check_can_create_quotation()

    def test_rejected_output_allows_adding_and_editing_material(self):
        lead = self._lead()
        product = self.env['product.product'].create({'name': 'Demo correction product'})
        line = self.env['crm.material.line'].create({'lead_id': lead.id, 'product_id': product.id})
        request = self._request(lead)
        lead.approval_request_id = request
        picking = self._picking(lead, request)
        request.approver_ids.write({'status': 'pending'})
        request.action_reject_demo()
        line.write({'quantity': 3})
        self.env['crm.material.line'].create({'lead_id': lead.id, 'product_id': product.id})
        lead._ensure_material_request_allowed()
        self.assertEqual(line.quantity, 3)
        self.assertEqual(picking.state, 'cancel')
        self.assertEqual(lead.picking_count, 1)

    def test_active_output_still_blocks_material_changes(self):
        from odoo.exceptions import ValidationError
        lead = self._lead()
        product = self.env['product.product'].create({'name': 'Locked demo product'})
        line = self.env['crm.material.line'].create({'lead_id': lead.id, 'product_id': product.id})
        self._picking(lead, self._request(lead))
        with self.assertRaises(ValidationError):
            line.write({'quantity': 3})
        with self.assertRaises(ValidationError):
            self.env['crm.material.line'].create({'lead_id': lead.id, 'product_id': product.id})

    def test_smart_buttons_include_rejected_and_indirect_history(self):
        lead, other = self._lead(), self._lead()
        old = self._request(lead)
        lead.approval_request_id = old
        old.approver_ids.write({'status': 'pending'})
        rejected = self._picking(lead, old)
        old.action_reject_demo()
        current = self._request(lead)
        lead.approval_request_id = current
        indirect = self._picking(None, current)
        unrelated = self._picking(other, self._request(other))
        self.assertEqual(lead.demo_approval_count, 2)
        self.assertEqual(lead.picking_count, 2)
        self.assertEqual(set(lead.action_view_approval_request()['domain'][0][2]), {old.id, current.id})
        picking_ids = lead.action_view_pickings()['domain'][0][2]
        self.assertEqual(set(picking_ids), {rejected.id, indirect.id})
        self.assertNotIn(unrelated.id, picking_ids)

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

    def test_reject_approval_returns_to_crm_without_losing_opportunity(self):
        lead = self._lead()
        request = self._request(lead)
        lead.approval_request_id = request
        request.approver_ids.write({'status': 'pending'})
        action = request.action_reject_demo()
        self.assertEqual(action['res_id'], lead.id)
        self.assertTrue(lead.active)
        self.assertEqual(lead.probability, 60)
        self.assertFalse(lead.approval_request_id)
        self.assertEqual(request.request_status, 'refused')

    def test_reject_cancels_pending_output_and_preserves_history(self):
        lead = self._lead()
        request = self._request(lead)
        lead.approval_request_id = request
        picking = self._picking(lead, request)
        request.approver_ids.write({'status': 'pending'})
        request.action_reject_demo()
        self.assertEqual(picking.state, 'cancel')
        self.assertEqual(picking.material_approval_state, 'refused')
        self.assertEqual(picking.approval_request_id, request)
        self.assertTrue(lead.active)
        self.assertFalse(lead.approval_request_id)

    def test_stale_rejection_does_not_unlock_new_request(self):
        lead = self._lead()
        old = self._request(lead)
        current = self._request(lead)
        lead.approval_request_id = current
        old.approver_ids.write({'status': 'pending'})
        with self.assertRaises(UserError):
            old.action_reject_demo()
        self.assertEqual(lead.approval_request_id, current)

    def test_unassigned_user_cannot_cancel_or_reject(self):
        user = self.env['res.users'].create({'name': 'Demo unauthorized', 'login': 'demo_unauthorized_test',
            'group_ids': [Command.set([self.env.ref('base.group_user').id,
                self.env.ref('sales_team.group_sale_manager').id,
                self.env.ref('approvals.group_approval_manager').id,
                self.env.ref('stock.group_stock_manager').id])]})
        lead = self._lead()
        request = self._request(lead)
        lead.approval_request_id = request
        request.approver_ids.write({'status': 'pending'})
        picking = self._picking(lead, request)
        self.assertFalse(lead.with_user(user).can_cancel_demo)
        self.assertFalse(request.with_user(user).can_reject_demo)
        for record, method in [(lead, 'action_cancel_demo'), (request, 'action_cancel'),
                               (request, 'action_reject_demo'), (request, 'action_refuse'),
                               (picking, 'action_cancel'), (picking, 'action_audit_refuse_material')]:
            with self.assertRaises(UserError):
                getattr(record.with_user(user), method)()

    def test_assigned_auditor_returns_output_to_crm(self):
        auditor = self.env['res.users'].create({'name': 'Assigned demo auditor', 'login': 'demo_assigned_auditor_test',
            'group_ids': [Command.set([self.env.ref('base.group_user').id,
                self.env.ref('stock.group_stock_manager').id,
                self.env.ref('sales_team.group_sale_manager').id])]})
        employee = self.env['hr.employee'].create({'name': 'Assigned auditor', 'user_id': auditor.id})
        lead = self._lead()
        request = self._request(lead, material_auditor_ids=[Command.set(employee.ids)])
        lead.approval_request_id = request
        request.approver_ids.write({'status': 'pending'})
        picking = self._picking(lead, request)
        self.assertTrue(picking.with_user(auditor).can_cancel_demo)
        action = picking.with_user(auditor).action_audit_refuse_material()
        self.assertEqual(action['res_id'], lead.id)
        self.assertEqual(picking.state, 'cancel')
        self.assertEqual(picking.material_auditor_id, auditor)
        self.assertFalse(lead.approval_request_id)
        self.assertTrue(lead.active)

    def test_chief_from_organigram_can_reject_approval(self):
        chief = self.env['res.users'].create({'name': 'Demo chief', 'login': 'demo_chief_test',
            'group_ids': [Command.set([self.env.ref('base.group_user').id,
                self.env.ref('sales_team.group_sale_manager').id,
                self.env.ref('approvals.group_approval_manager').id])]})
        boss = self.env['hr.employee'].create({'name': 'Demo chief', 'user_id': chief.id})
        employee = self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1)
        if not employee:
            employee = self.env['hr.employee'].create({'name': 'Demo owner', 'user_id': self.env.uid})
        employee.parent_id = boss
        lead = self._lead()
        lead.user_id = self.env.user
        request = self._request(lead)
        lead.approval_request_id = request
        request.approver_ids.write({'status': 'pending'})
        self.assertTrue(request.with_user(chief).can_reject_demo)
        request.with_user(chief).action_reject_demo()
        self.assertFalse(lead.approval_request_id)
        self.assertTrue(lead.active)
