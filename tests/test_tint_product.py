# -*- coding: utf-8 -*-

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTintProduct(TransactionCase):
    """ Configuración de entintado en los productos:

    - Una base resuelve su capacidad automáticamente desde la matriz, sin
      que nadie la capture.
    - Una base mal configurada falla al guardarse, no en el momento de la
      venta frente al cliente.
    - Un colorante debe medirse en una unidad compatible con el punto.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.templates = cls.env['product.template']
        cls.base_types = cls.env['tint.base.type']
        cls.sizes = cls.env['tint.size']
        cls.point = cls.env.ref('entintados_pdv.uom_tint_point')
        cls.ounce = cls.env.ref('entintados_pdv.uom_tint_ounce')
        cls.unit = cls.env.ref('uom.product_uom_unit')

        cls.white = cls.base_types.search([('code', '=', 'W')], limit=1)
        cls.deep = cls.base_types.search([('code', '=', 'D')], limit=1)
        cls.yellow = cls.base_types.search([('code', '=', 'Y')], limit=1)
        cls.liter = cls.sizes.search([('code', '=', 'L')], limit=1)
        cls.gallon = cls.sizes.search([('code', '=', 'G')], limit=1)
        cls.bucket = cls.sizes.search([('code', '=', 'Q')], limit=1)

    def _create_base(self, base_type, size, **values):
        values.update({
            'name': values.get('name', 'Base de prueba'),
            'tint_role': 'base',
            'tint_base_type_id': base_type.id,
            'tint_size_id': size.id,
        })
        return self.templates.create(values)

    def _create_colorant(self, **values):
        values.setdefault('name', 'Colorante de prueba')
        values.setdefault('uom_id', self.point.id)
        values['tint_role'] = 'colorant'
        return self.templates.create(values)

    # --- Capacidad resuelta desde la matriz ----------------------------

    def test_base_resolves_capacity_from_matrix(self):
        """Nadie captura la capacidad: se deriva del tipo y la presentación."""
        base = self._create_base(self.white, self.bucket)
        self.assertEqual(base.tint_capacity_points, 456)
        self.assertEqual(base.tint_capacity_display, '9Y 24')

    def test_capacity_follows_the_matrix_for_every_size(self):
        expected = {'L': 96, 'G': 384, 'Q': 1824}
        for code, points in expected.items():
            size = self.sizes.search([('code', '=', code)], limit=1)
            base = self._create_base(self.deep, size, name='Deep %s' % code)
            self.assertEqual(base.tint_capacity_points, points)

    def test_capacity_updates_when_size_changes(self):
        base = self._create_base(self.white, self.liter)
        self.assertEqual(base.tint_capacity_points, 24)
        base.tint_size_id = self.gallon
        self.assertEqual(base.tint_capacity_points, 96)
        self.assertEqual(base.tint_capacity_display, '2Y')

    def test_capacity_follows_matrix_edits(self):
        """Si se corrige la matriz, la capacidad del producto se actualiza."""
        base = self._create_base(self.white, self.liter)
        capacity = self.env['tint.base.capacity'].search([
            ('base_type_id', '=', self.white.id),
            ('size_id', '=', self.liter.id),
        ], limit=1)
        capacity.max_points = 30
        base.invalidate_recordset(['tint_capacity_points'])
        self.assertEqual(base.tint_capacity_points, 30)

    def test_non_tint_product_has_no_capacity(self):
        product = self.templates.create({'name': 'Brocha de 4 pulgadas'})
        self.assertFalse(product.tint_role)
        self.assertEqual(product.tint_capacity_points, 0)
        self.assertFalse(product.tint_capacity_display)

    # --- Validaciones al guardar ---------------------------------------

    def test_base_without_type_fails(self):
        with self.assertRaises(ValidationError):
            self.templates.create({
                'name': 'Base incompleta',
                'tint_role': 'base',
                'tint_size_id': self.liter.id,
            })

    def test_base_without_size_fails(self):
        with self.assertRaises(ValidationError):
            self.templates.create({
                'name': 'Base incompleta',
                'tint_role': 'base',
                'tint_base_type_id': self.white.id,
            })

    def test_base_with_combination_absent_from_matrix_fails(self):
        """El error se detecta al capturar el catálogo, no en la caja."""
        new_size = self.sizes.create({
            'name': 'Medio litro', 'code': 'H', 'volume_liters': 0.5,
        })
        with self.assertRaises(Exception):
            self._create_base(self.white, new_size, name='Base sin capacidad')

    def test_colorant_with_wrong_uom_fails(self):
        with self.assertRaises(ValidationError):
            self._create_colorant(uom_id=self.unit.id)

    def test_colorant_accepts_ounce_uom(self):
        """La onza comparte referencia con el punto, así que es válida."""
        colorant = self._create_colorant(
            name='Colorante en onzas', uom_id=self.ounce.id)
        self.assertEqual(colorant.uom_id, self.ounce)

    def test_colorant_fields(self):
        colorant = self._create_colorant(list_price=2.5)
        self.assertEqual(colorant.list_price, 2.5)
        self.assertEqual(colorant.uom_id, self.point)

    # --- Extracción previa ---------------------------------------------

    def test_extraction_volume_for_line_color_base(self):
        """Yellow en galón: 10% de 4 litros nominales."""
        base = self._create_base(self.yellow, self.gallon, name='Base Yellow galón')
        self.assertTrue(base.tint_requires_extraction)
        self.assertAlmostEqual(base.tint_extraction_liters, 0.4, places=3)
        self.assertTrue(base.tint_operator_note)

    def test_extraction_volume_for_bucket(self):
        base = self._create_base(self.yellow, self.bucket, name='Base Yellow cubeta')
        self.assertAlmostEqual(base.tint_extraction_liters, 1.9, places=3)

    def test_no_extraction_for_regular_base(self):
        base = self._create_base(self.white, self.gallon)
        self.assertFalse(base.tint_requires_extraction)
        self.assertEqual(base.tint_extraction_liters, 0.0)

    # --- Asistencia del formulario -------------------------------------

    def test_onchange_role_colorant_sets_point_uom(self):
        product = self.templates.new({'name': 'Nuevo colorante'})
        product.tint_role = 'colorant'
        product._onchange_tint_role()
        self.assertEqual(product.uom_id, self.point)

    def test_onchange_role_clears_opposite_fields(self):
        product = self.templates.new({
            'name': 'Producto cambiante',
            'tint_role': 'base',
            'tint_base_type_id': self.white.id,
            'tint_size_id': self.liter.id,
        })
        product.tint_role = 'colorant'
        product._onchange_tint_role()
        self.assertFalse(product.tint_base_type_id)
        self.assertFalse(product.tint_size_id)
