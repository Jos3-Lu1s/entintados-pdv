from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMaterialChatter(TransactionCase):
    def setUp(self):
        super().setUp()
        self.lead = self.env['crm.lead'].with_context(tracking_disable=True).create({
            'name': 'Bitácora materiales', 'type': 'opportunity',
        }).with_context(tracking_disable=False)
        self.product = self.env['product.product'].create({
            'name': 'Producto bitácora', 'type': 'consu', 'is_storable': True,
        })

    def test_material_create_edit_noop_delete(self):
        before = self.lead.message_ids
        line = self.env['crm.material.line'].create({
            'lead_id': self.lead.id, 'product_id': self.product.id,
            'quantity': 1, 'uom_id': self.product.uom_id.id,
        })
        self.assertEqual(len(self.lead.message_ids - before), 1)
        before = self.lead.message_ids
        line.write({'quantity': 3, 'description': '<script>unsafe</script>'})
        notes = self.lead.message_ids - before
        self.assertEqual(len(notes), 1)
        self.assertIn('1.0 → 3.0', notes.body)
        self.assertNotIn('<script>', notes.body)
        self.assertEqual(notes.author_id, self.env.user.partner_id)
        before = self.lead.message_ids
        line.write({'quantity': 3})
        self.assertFalse(self.lead.message_ids - before)
        line.unlink()
        self.assertEqual(len(self.lead.message_ids - before), 1)

    def test_native_and_extra_tracking_are_a_single_set(self):
        tracked = self.lead._track_get_fields()
        self.assertIn('name', tracked)
        self.assertIn('wharehouse_id', tracked)
        self.assertNotIn('write_date', tracked)
        self.assertNotIn('material_line_ids', tracked)
        location = self.env.ref('stock.stock_location_stock')
        self.lead.write({'wharehouse_id': location.id})
        self.lead._track_finalize()
        changes = self.lead.message_ids.tracking_value_ids.filtered(
            lambda value: value.field_id.name == 'wharehouse_id')
        self.assertEqual(len(changes), 1)

    def test_binary_replacement_without_binary_content_in_chatter(self):
        picking = self.env['stock.picking'].with_context(tracking_disable=True).create({
            'picking_type_id': self.env.ref('stock.picking_type_out').id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
        }).with_context(tracking_disable=False)
        before = picking.message_ids
        picking.write({'document_file': b'c2VjcmV0'})
        notes = picking.message_ids - before
        self.assertEqual(len(notes), 1)
        self.assertNotIn('c2VjcmV0', notes.body)
        before = picking.message_ids
        picking.write({'document_file': b'c2VjcmV0'})
        self.assertFalse(picking.message_ids - before)

    def test_tracking_disabled_is_respected(self):
        before = self.lead.message_ids
        self.env['crm.material.line'].with_context(tracking_disable=True).create({
            'lead_id': self.lead.id, 'product_id': self.product.id,
            'quantity': 1, 'uom_id': self.product.uom_id.id,
        })
        self.assertFalse(self.lead.message_ids - before)
