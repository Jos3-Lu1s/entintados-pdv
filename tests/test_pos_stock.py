from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPosStock(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env['pos.config'].create({'name': 'Stock TPV Test'})
        cls.product = cls.env['product.product'].create({
            'name': 'Producto con inventario TPV', 'is_storable': True,
            'available_in_pos': True,
        })
        cls.location = cls.config.picking_type_id.default_location_src_id
        cls.env['stock.quant']._update_available_quantity(cls.product, cls.location, 3)

    def order(self, quantities, product=None):
        return self.env['pos.order'].new({
            'config_id': self.config.id,
            'company_id': self.config.company_id.id,
            'lines': [(0, 0, {'product_id': (product or self.product).id, 'qty': qty})
                      for qty in quantities],
        })

    def test_stock_scoped_to_pos_location(self):
        other = self.env['stock.location'].create({
            'name': 'Otro almacén', 'usage': 'internal',
        })
        self.env['stock.quant']._update_available_quantity(self.product, other, 20)
        self.assertEqual(self.product.get_pos_stock(self.config.id)[self.product.id], 3)

    def test_exact_available_quantity(self):
        self.order([3])._check_pos_stock()

    def test_aggregate_lines_exceed_stock(self):
        with self.assertRaises(UserError):
            self.order([2, 2])._check_pos_stock()

    def test_refund_does_not_supply_stock(self):
        self.order([-4])._check_pos_stock()
        with self.assertRaises(UserError):
            self.order([4, -1])._check_pos_stock()

    def test_untracked_product(self):
        service = self.env['product.product'].create({'name': 'Servicio TPV', 'type': 'service'})
        self.order([100], service)._check_pos_stock()

    def test_zero_stock(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.location, -3)
        with self.assertRaises(UserError):
            self.order([1])._check_pos_stock()
