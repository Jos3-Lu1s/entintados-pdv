from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.convert import convert_file
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestPosLegacyViewCleanup(TransactionCase):
    def test_single_split_invoice_opens_without_main_invoice(self):
        partner = self.env['res.partner'].create({'name': 'Factura dividida QA'})
        invoice = self.env['account.move'].create({'move_type': 'out_invoice', 'partner_id': partner.id})
        order = self.env['pos.order'].new({'split_invoice_ids': [(6, 0, invoice.ids)]})
        self.assertEqual(order.entintados_invoice_count, 1)
        action = order.action_view_invoice()
        self.assertEqual(action['res_model'], 'account.move')
        self.assertEqual(action['res_id'], order.split_invoice_ids.id)
        self.assertEqual(order.split_invoice_ids._origin, invoice)

    def test_invoice_and_delivery_buttons_remain_in_order_form(self):
        convert_file(self.env, 'entintados_pdv', 'views/pos_order_views.xml', {}, mode='update')
        view = self.env.ref('point_of_sale.view_pos_pos_form')
        view._check_xml()
        arch = view._get_combined_arch()
        invoice = arch.xpath("//div[@name='button_box']/button[@name='action_view_invoice']")
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice[0].get('invisible'), 'entintados_invoice_count == 0')
        self.assertEqual(invoice[0].get('string'), 'Recibo/Factura')
        pending = arch.xpath("//div[@name='button_box']/button[@name='action_pos_order_invoice']")
        self.assertEqual(len(pending), 1)
        for state in ('paid', 'done'):
            self.assertFalse(safe_eval(pending[0].get('invisible'), {'state': state, 'entintados_invoice_count': 0}))
            self.assertTrue(safe_eval(pending[0].get('invisible'), {'state': state, 'entintados_invoice_count': 1}))
            self.assertFalse(safe_eval(invoice[0].get('invisible'), {'entintados_invoice_count': 1}))
        self.assertTrue(arch.xpath("//div[@name='button_box']/button[@name='action_stock_picking']"))
        self.assertTrue(arch.xpath("//div[@name='button_box']/button[@name='action_view_refund_orders']"))
        header = arch.xpath("//header/button[@name='action_pos_order_invoice']")
        self.assertEqual(len(header), 1)
        self.assertIn('entintados_invoice_count > 0', header[0].get('invisible'))
        self.assertTrue(arch.xpath("//header/field[@name='state'][@widget='statusbar']"))
        self.assertTrue(arch.xpath("//field[@name='invoice_status'][@widget='badge']"))
        list_arch = self.env.ref('point_of_sale.view_pos_order_tree')._get_combined_arch()
        self.assertEqual(list_arch.xpath("//field[@name='invoice_status']")[0].get('invisible'), 'False')

    def test_accounting_ticket_views_do_not_replace_default_pos_views(self):
        convert_file(self.env, 'entintados_pdv', 'views/pos_accounting_return_views.xml', {}, mode='update')
        View = self.env['ir.ui.view']
        self.assertEqual(View.default_view('pos.order', 'form'), self.env.ref('point_of_sale.view_pos_pos_form').id)
        self.assertEqual(View.default_view('pos.order', 'list'), self.env.ref('point_of_sale.view_pos_order_tree').id)
        action = self.env.ref('entintados_pdv.pos_accounting_ticket_action_form')
        self.assertEqual(action.view_id, self.env.ref('entintados_pdv.pos_accounting_ticket_form'))

    def test_upgrade_retires_stale_reference_view(self):
        convert_file(self.env, 'entintados_pdv', 'views/pos_order_legacy_cleanup.xml', {}, mode='update')
        view = self.env.ref('entintados_pdv.view_pos_order_form_previous_reference')
        old_arch = '<xpath expr="//field[@name=\'pos_reference\']" position="after"><field name="previous_order_reference"/></xpath>'
        # Reproduce a view persisted by an older version, whose field is now absent.
        self.env.cr.execute("UPDATE ir_ui_view SET active = true, arch_db = jsonb_build_object('en_US', %s) WHERE id = %s",
                            [old_arch, view.id])
        view.invalidate_recordset()
        self.env.registry.clear_cache()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            view._check_xml()
        convert_file(self.env, 'entintados_pdv', 'views/pos_order_legacy_cleanup.xml', {}, mode='update')
        self.assertFalse(view.active)
        self.assertNotIn('previous_order_reference', view.arch_db)
        self.env.ref('point_of_sale.view_pos_pos_form')._check_xml()
        convert_file(self.env, 'entintados_pdv', 'views/pos_order_legacy_cleanup.xml', {}, mode='update')
        self.assertFalse(view.active)
