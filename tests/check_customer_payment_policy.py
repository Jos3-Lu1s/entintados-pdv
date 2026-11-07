"""Validate required payment data and credit-only PPD; all writes rolled back."""
import os
import sys
import unittest
import odoo
if not hasattr(odoo, 'Command'):
    import odoo.init
from odoo.exceptions import ValidationError
from odoo.modules.registry import Registry
from odoo.tools import config

database = sys.argv[1]
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http',
                     '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
                     '--db_password=' + os.environ['PASSWORD']])
Registry(database)
from odoo.tests import common
common.get_db_name = lambda: database


@common.tagged('post_install', '-at_install')
class CustomerPaymentPolicy(common.TransactionCase):
    def test_sale_defaults_required_credit_and_view(self):
        customer = self.env['res.partner'].create({'name': 'Cliente Ventas QA',
            'phone': '5551234567', 'is_customer': True})
        order = self.env['sale.order'].create({'partner_id': customer.id})
        self.assertEqual(order.l10n_mx_edi_payment_policy, 'PUE')
        self.assertEqual(order.l10n_mx_edi_payment_method_id,
                         self.env.ref('l10n_mx_edi.payment_method_efectivo'))
        for values in ({'l10n_mx_edi_payment_policy': False},
                       {'l10n_mx_edi_payment_method_id': False},
                       {'l10n_mx_edi_payment_policy': 'PPD'}):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                order.write(values)
        customer.is_credit = True
        days = self.env['account.payment.term'].create({'name': '30 días política QA',
            'line_ids': [(0, 0, {'value': 'percent', 'value_amount': 100, 'nb_days': 30})]})
        order.write({'l10n_mx_edi_payment_policy': 'PPD', 'payment_term_id': days.id})
        customer.is_credit = False
        with self.assertRaises(ValidationError), self.cr.savepoint():
            order.action_confirm()
        order.l10n_mx_edi_payment_policy = 'PUE'
        self.cr.execute('UPDATE sale_order SET l10n_mx_edi_payment_policy = NULL, l10n_mx_edi_payment_method_id = NULL WHERE id = %s', [order.id])
        order.invalidate_recordset()
        self.env['sale.order']._setup_sale_payment_defaults()
        self.assertTrue(order.l10n_mx_edi_payment_method_id)
        self.assertEqual(order.l10n_mx_edi_payment_policy, 'PUE')
        from odoo.tools.convert import convert_file
        convert_file(self.env, 'entintados_pdv', 'views/sale_order_views.xml', {}, mode='update')
        self.assertIn('sale_customer_payment_policy',
                      self.env.ref('entintados_pdv.sale_order_payment_policy_required').arch_db)

    def test_snapshot_is_private_and_existing_message_is_redacted(self):
        from markupsafe import Markup
        from odoo.addons.entintados_pdv.models.pos_sale_quotation import _QUOTATION_OPERATION
        customer = self.env['res.partner'].create({'name': 'Privacidad QA',
            'phone': '5551234567', 'is_customer': True})
        order = self.env['sale.order'].create({'partner_id': customer.id})
        order.with_context(_quotation_operation=_QUOTATION_OPERATION).write({
            'pos_quotation_snapshot': {'lines': [['PRIVATE-TEST-SNAPSHOT', 40]]},
            'pos_quotation_uuid': 'private-test-uuid'})
        bodies = ''.join(str(message.body) for message in order.message_ids)
        self.assertNotIn('PRIVATE-TEST-SNAPSHOT', bodies)
        self.assertNotIn('private-test-uuid', bodies)
        self.assertNotIn('pos_quotation_uuid', order._track_get_fields())
        old = order._message_log(body=Markup('<p class="o_business_audit">Origen: Ventas</p>'
            '<ul><li>Pos Quotation Snapshot: false → PRIVATE-OLD-SNAPSHOT</li><li>Estado: Cotización</li></ul>'))
        tracking = self.env['mail.tracking.value'].create({
            'mail_message_id': old.id,
            'field_id': self.env['ir.model.fields']._get('sale.order', 'pos_quotation_uuid').id,
            'new_value_char': 'PRIVATE-TRACKING'})
        order._redact_private_chatter_details()
        self.assertFalse(tracking.exists())
        self.assertNotIn('PRIVATE-OLD-SNAPSHOT', old.body)
        self.assertIn('Estado: Cotización', old.body)

    def test_defaults_required_and_credit(self):
        Partner = self.env['res.partner']
        customer = Partner.create({'name': 'Política cliente QA', 'phone': '5551234567', 'is_customer': True})
        self.assertIn('MX', customer.fiscal_country_codes)
        self.assertFalse(customer.l10n_mx_edi_payment_policy)
        self.assertFalse(customer.l10n_mx_edi_payment_method_id)
        for values in ({'l10n_mx_edi_payment_policy': False},
                       {'l10n_mx_edi_payment_method_id': False},
                       {'l10n_mx_edi_payment_policy': 'PPD'}):
            customer.write(values)
        order = self.env['sale.order'].create({'partner_id': customer.id})
        self.assertEqual(order.l10n_mx_edi_payment_policy, 'PUE')
        customer.write({'is_credit': True, 'l10n_mx_edi_payment_policy': 'PPD'})
        self.assertTrue(customer.payment_credit_authorized)
        customer.is_credit = False
        customer.write({'is_credit': False, 'l10n_mx_edi_payment_policy': 'PUE'})
        customer.l10n_mx_edi_payment_policy = 'PUE'

    def test_parent_credit_and_view(self):
        parent = self.env['res.partner'].create({'name': 'Crédito comercial QA',
            'phone': '5551234567', 'is_company': True, 'is_customer': True, 'is_credit': True})
        child = self.env['res.partner'].create({'name': 'Contacto crédito QA', 'phone': '5551234567',
            'parent_id': parent.id, 'is_customer': True, 'l10n_mx_edi_payment_policy': 'PPD'})
        self.assertTrue(child.payment_credit_authorized)
        parent.is_credit = False
        self.assertFalse(child.payment_credit_authorized)
        from odoo.tools.convert import convert_file
        convert_file(self.env, 'entintados_pdv', 'views/res_partner_views.xml', {}, mode='update')
        view = self.env.ref('entintados_pdv.view_partner_form_custom_fiscal_field')
        self.assertNotIn('customer_payment_policy', view.arch_db)
        self.assertNotIn('name="required"', view.arch_db)


result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CustomerPaymentPolicy))
raise SystemExit(0 if result.wasSuccessful() else 1)
