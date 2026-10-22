from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCrmMaterialStock(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.location = cls.env['stock.location'].create({'name': 'Material CRM origen', 'usage': 'internal'})
        cls.other_location = cls.env['stock.location'].create({'name': 'Material CRM otro', 'usage': 'internal'})
        cls.product = cls.env['product.product'].create({
            'name': 'Material CRM con inventario', 'type': 'consu', 'is_storable': True,
        })
        cls.lead = cls.env['crm.lead'].create({
            'name': 'Validación de stock CRM', 'wharehouse_id': cls.location.id,
        })

    def values(self):
        return {'lead_id': self.lead.id, 'product_id': self.product.id,
                'quantity': 1, 'uom_id': self.product.uom_id.id}

    def test_zero_stock_allows_saving_but_blocks_request(self):
        line = self.env['crm.material.line'].create(self.values())
        line.write({'quantity': 2})
        self.lead.write({'name': 'Oportunidad guardada sin stock'})
        self.assertEqual(line.quantity, 2)
        with self.assertRaises(ValidationError):
            line._check_material_stock()

    def test_stock_in_another_location_does_not_allow_request(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.other_location, 5)
        line = self.env['crm.material.line'].create(self.values())
        with self.assertRaises(ValidationError):
            line._check_material_stock()

    def test_available_stock_and_later_depletion(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.location, 2)
        line = self.env['crm.material.line'].create(self.values())
        line._check_material_stock()
        self.env['stock.quant']._update_available_quantity(self.product, self.location, -2)
        with self.assertRaises(ValidationError):
            line._check_material_stock()

    def test_no_origin_defers_validation(self):
        lead = self.env['crm.lead'].create({'name': 'Material sin origen'})
        values = dict(self.values(), lead_id=lead.id)
        self.env['crm.material.line'].create(values)
