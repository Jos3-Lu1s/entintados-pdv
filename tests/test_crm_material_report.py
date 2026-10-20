from odoo import Command, fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCrmMaterialReport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Material report regression', 'default_code': 'REPORT-MATERIAL',
            'type': 'consu', 'is_storable': True,
        })
        cls.lead = cls.env['crm.lead'].create({
            'name': 'CRM material report regression', 'type': 'opportunity',
            'user_id': cls.env.uid,
            'material_line_ids': [Command.create({
                'product_id': cls.product.id, 'quantity': 3,
                'uom_id': cls.product.uom_id.id, 'description': 'Detalles de uso CRM',
            })],
        })

    def _picking(self):
        request = self.env['approval.request'].create({
            'name': 'Report approval', 'category_id': self.env.ref('entintados_pdv.approval_category_salida_material').id,
            'crm_lead_id': self.lead.id, 'request_owner_id': self.env.uid,
        })
        return self.env['stock.picking'].create({
            'picking_type_id': self.env.ref('entintados_pdv.picking_type_material_output').id,
            'crm_lead_id': self.lead.id, 'approval_request_id': request.id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            'material_auditor_id': self.env.uid, 'signature_date': fields.Datetime.now(),
            'move_ids': [Command.create({
                'product_id': self.product.id, 'product_uom_qty': 2,
                'product_uom': self.product.uom_id.id, 'description_picking': 'Detalles de uso salida',
                'location_id': self.env.ref('stock.stock_location_stock').id,
                'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            })],
        })

    def _assert_report(self, xmlid, record):
        report = self.env.ref(xmlid)
        self.assertEqual(report.report_type, 'qweb-pdf')
        # Render the actual download template without wkhtmltopdf's external
        # HTTP requests, which cannot share the test's uncommitted transaction.
        content, kind = report._render_qweb_html(report.report_name, [record.id])
        self.assertEqual(kind, 'html')
        self.assertIn(b'REPORT-MATERIAL', content)

    def test_crm_values_before_stock_output_exists(self):
        values = self.lead._get_material_report_values()
        self.assertEqual(values['lead'], self.lead)
        self.assertFalse(values['picking'])
        self.assertEqual(values['lines'], self.lead.material_line_ids)
        self.assertEqual(values['salesperson'], self.env.user)

    def test_crm_uses_current_auditor_and_keeps_crm_material_lines(self):
        picking = self._picking()
        values = self.lead._get_material_report_values()
        self.assertEqual(values['picking'], picking)
        self.assertEqual(values['approver'], picking.material_auditor_id)
        self.assertEqual(values['signature_date'], picking.signature_date)
        self.assertEqual(values['lines'][0].quantity, 3)
        self.assertEqual(values['lines']._name, 'crm.material.line')

    def test_pdf_download_from_crm_before_stock_output(self):
        self._assert_report('entintados_pdv.action_report_material_crm_lead', self.lead)

    def test_pdf_download_from_crm_with_stock_output(self):
        self._picking()
        self._assert_report('entintados_pdv.action_report_material_crm_lead', self.lead)

    def test_pdf_download_from_stock_output_is_preserved(self):
        picking = self._picking()
        values = picking._get_material_report_values()
        self.assertEqual(values['lines'], picking.move_ids)
        self.assertEqual(values['lines'][0].product_uom_qty, 2)
        self._assert_report('entintados_pdv.action_report_material_stock_picking', picking)
