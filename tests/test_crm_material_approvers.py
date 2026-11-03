from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCrmMaterialApprovers(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.category = cls.env.ref('entintados_pdv.approval_category_salida_material')
        cls.direction = cls.env.ref('entintados_pdv.hr_department_direccion_comercial')
        cls.intelligence = cls.env.ref('entintados_pdv.hr_department_inteligencia_comercial')
        cls.users = cls.env['res.users'].with_context(no_reset_password=True).create([
            {'name': name, 'login': login, 'company_id': cls.company.id,
             'company_ids': [(6, 0, cls.company.ids)]}
            for name, login in [('Vendedor materiales', 'material-seller-test'),
                                ('Jefe comercial', 'material-manager-test'),
                                ('Inteligencia comercial', 'material-intelligence-test')]
        ])
        cls.seller, cls.manager, cls.analyst = cls.users
        manager_employee = cls.env['hr.employee'].create({
            'name': 'Jefe comercial', 'user_id': cls.manager.id,
            'department_id': cls.direction.id, 'company_id': cls.company.id,
        })
        cls.env['hr.employee'].create([
            {'name': 'Vendedor materiales', 'user_id': cls.seller.id,
             'parent_id': manager_employee.id, 'company_id': cls.company.id},
            {'name': 'Analista comercial', 'user_id': cls.analyst.id,
             'department_id': cls.intelligence.id, 'company_id': cls.company.id},
        ])
        cls.lead = cls.env['crm.lead'].create({
            'name': 'Aprobadores comerciales', 'user_id': cls.seller.id,
            'company_id': cls.company.id, 'type': 'opportunity',
        })

    def test_departments_and_manager_without_duplicates(self):
        approvers = self.lead._get_material_approvers()
        self.assertIn(self.manager, approvers)
        self.assertIn(self.analyst, approvers)
        self.assertEqual(approvers.ids.count(self.manager.id), 1)

    def test_one_commercial_approval_is_sufficient(self):
        self.assertEqual(self.category.approval_minimum, 1)
        self.assertFalse(self.category.approver_sequence)
        self.assertFalse(self.category.manager_approval)
        auditor = self.env['hr.employee'].search([
            ('user_id', '=', self.env.uid), ('company_id', '=', self.company.id),
        ], limit=1)
        if not auditor:
            auditor = self.env['hr.employee'].create({
                'name': 'Auditor materiales test', 'user_id': self.env.uid,
                'company_id': self.company.id,
            })
        request = self.env['approval.request'].create({
            'name': 'Solicitud comercial test', 'category_id': self.category.id,
            'request_owner_id': self.seller.id, 'crm_lead_id': self.lead.id,
            'material_auditor_ids': [(6, 0, auditor.ids)],
            'approver_ids': [(0, 0, {'user_id': user.id, 'required': False})
                             for user in (self.manager | self.analyst)],
        })
        request.approver_ids.write({'status': 'pending'})
        selected = request.approver_ids.filtered(lambda row: row.user_id == self.analyst)
        with patch.object(type(request), '_create_material_picking', return_value=False) as create_picking:
            request.action_approve(approver=selected)
            self.assertEqual(request.request_status, 'approved')
            self.assertEqual(len(request.approver_ids.filtered(lambda row: row.status == 'approved')), 1)
            create_picking.assert_called_once()
