from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPosContactInfo(TransactionCase):
    def test_contact_data_and_default_addresses_are_exported_without_changes(self):
        customer = self.env['res.partner'].create({
            'name': 'Cliente datos PdV', 'is_company': True, 'is_customer': True,
            'phone': '5551234567', 'ref': '7804', 'vat': 'XAXX010101000',
            'l10n_mx_edi_fiscal_regime': '601', 'country_id': self.env.ref('base.mx').id,
            'street': 'Calle principal 10', 'zip': '72430', 'city': 'Puebla',
            'user_id': self.env.user.id,
        })
        invoice = self.env['res.partner'].create({'name': 'Oficina fiscal', 'parent_id': customer.id,
            'type': 'invoice', 'street': 'Calle fiscal 20', 'phone': '5551234567'})
        delivery = self.env['res.partner'].create({'name': 'Bodega cliente', 'parent_id': customer.id,
            'type': 'delivery', 'street': 'Calle entrega 30', 'phone': '5551234567'})
        info = customer.pos_contact_info
        self.assertEqual(info['reference'], '7804')
        self.assertEqual(info['vat'], customer.vat)
        self.assertEqual(info['salesperson'], self.env.user.name)
        self.assertIn('Calle principal 10', info['address'])
        self.assertIn('72430', info['address'])
        self.assertTrue(info['fiscal_regime'])
        self.assertEqual(info['invoice']['name'], invoice.name)
        self.assertIn(invoice.street, info['invoice']['address'])
        self.assertEqual(info['delivery']['name'], delivery.name)
        invoice.street = 'Dirección fiscal actualizada'
        self.assertIn(invoice.street, customer.pos_contact_info['invoice']['address'])
        self.assertEqual(customer.ref, '7804')
        self.assertEqual(customer.street, 'Calle principal 10')

    def test_missing_data_is_safe_and_pos_fields_keep_existing_exports(self):
        customer = self.env['res.partner'].create({'name': 'Cliente mínimo PdV', 'phone': '5551234567'})
        info = customer.pos_contact_info
        self.assertEqual(info['reference'], '')
        self.assertEqual(info['vat'], '')
        self.assertEqual(info['address'], '')
        self.assertEqual(info['invoice']['name'], customer.name)
        fields = customer._load_pos_data_fields(self.env['pos.config'])
        self.assertIn('pos_contact_info', fields)
        self.assertIn('sale_owner_name', fields)
        self.assertIn('name', fields)
        self.assertEqual(len(fields), len(set(fields)))
