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
        values.setdefault('is_storable', True)
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
        values.setdefault('is_storable', True)
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
                'is_storable': True,
            })

    def test_base_without_size_fails(self):
        with self.assertRaises(ValidationError):
            self.templates.create({
                'name': 'Base incompleta',
                'tint_role': 'base',
                'tint_base_type_id': self.white.id,
                'is_storable': True,
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
        schema = self.env['tint.schema'].create({'name': 'Esquema Test'})
        line = self.env['lines.product'].create({'name': 'Línea Test', 'scheme': schema.id})
        product = self.templates.new({
            'name': 'Producto cambiante',
            'tint_role': 'base',
            'tint_base_type_id': self.white.id,
            'tint_size_id': self.liter.id,
            'lines_product_id': line.id,
            'is_storable': True,
        })
        product.tint_role = 'colorant'
        product._onchange_tint_role()
        self.assertFalse(product.tint_base_type_id)
        self.assertFalse(product.tint_size_id)
        self.assertFalse(product.lines_product_id)

    def test_colorant_cannot_have_lines_product_id(self):
        schema = self.env['tint.schema'].create({'name': 'Esquema Test'})
        line = self.env['lines.product'].create({'name': 'Línea Test', 'scheme': schema.id})
        with self.assertRaises(ValidationError):
            self._create_colorant(name='Colorante Con Línea', lines_product_id=line.id)

    # --- Restricción de tipo de producto para entintado ----------------

    def test_tint_role_requires_consu_type_and_storable(self):
        """Bases y colorantes solo pueden ser de tipo 'consu' y con is_storable=True."""
        # 1. Base con tipo servicio
        with self.assertRaises(ValidationError):
            self.templates.create({
                'name': 'Base Servicio',
                'type': 'service',
                'is_storable': False,
                'tint_role': 'base',
                'tint_base_type_id': self.white.id,
                'tint_size_id': self.liter.id,
            })

        # 2. Colorante con tipo combo
        with self.assertRaises(ValidationError):
            self.templates.create({
                'name': 'Colorante Combo',
                'type': 'combo',
                'is_storable': False,
                'tint_role': 'colorant',
                'uom_id': self.point.id,
            })

        # 3. Base tipo consu pero no almacenable (is_storable=False)
        with self.assertRaises(ValidationError):
            self.templates.create({
                'name': 'Base Consumible No Almacenable',
                'type': 'consu',
                'is_storable': False,
                'tint_role': 'base',
                'tint_base_type_id': self.white.id,
                'tint_size_id': self.liter.id,
            })

        # 4. Colorante tipo consu pero no almacenable
        with self.assertRaises(ValidationError):
            self.templates.create({
                'name': 'Colorante No Almacenable',
                'type': 'consu',
                'is_storable': False,
                'tint_role': 'colorant',
                'uom_id': self.point.id,
            })

    def test_tint_role_allowed_for_storable_consu(self):
        """Bases y colorantes se crean exitosamente si son consu y almacenables."""
        base = self._create_base(self.white, self.liter, name='Base Válida', type='consu', is_storable=True)
        self.assertEqual(base.tint_role, 'base')
        self.assertEqual(base.type, 'consu')
        self.assertTrue(base.is_storable)

        colorant = self._create_colorant(name='Colorante Válido', type='consu', is_storable=True)
        self.assertEqual(colorant.tint_role, 'colorant')
        self.assertEqual(colorant.type, 'consu')
        self.assertTrue(colorant.is_storable)

    def test_changing_product_to_non_storable_fails_validation(self):
        """Modificar una base existente para cambiar tipo a servicio o desmarcar almacenable falla."""
        base = self._create_base(self.white, self.liter, name='Base Existente')

        with self.assertRaises(ValidationError):
            base.write({'type': 'service'})

        with self.assertRaises(ValidationError):
            base.write({'is_storable': False})

    def test_onchange_type_or_storable_clears_tint_role(self):
        """Al cambiar interactivamente en formulario a servicio o no almacenable, se limpia entintado."""
        schema = self.env['tint.schema'].create({'name': 'Esquema Onchange'})
        line = self.env['lines.product'].create({'name': 'Línea Onchange', 'scheme': schema.id})

        # Caso 1: Cambiar tipo a service
        product1 = self.templates.new({
            'name': 'Producto Formulario 1',
            'type': 'consu',
            'is_storable': True,
            'tint_role': 'base',
            'tint_base_type_id': self.white.id,
            'tint_size_id': self.liter.id,
            'lines_product_id': line.id,
        })
        product1.type = 'service'
        product1._onchange_tint_product_type()
        self.assertFalse(product1.tint_role)
        self.assertFalse(product1.tint_base_type_id)
        self.assertFalse(product1.tint_size_id)
        self.assertFalse(product1.lines_product_id)

        # Caso 2: Desmarcar is_storable
        product2 = self.templates.new({
            'name': 'Producto Formulario 2',
            'type': 'consu',
            'is_storable': True,
            'tint_role': 'colorant',
            'uom_id': self.point.id,
        })
        product2.is_storable = False
        product2._onchange_tint_product_type()
        self.assertFalse(product2.tint_role)

    def test_regular_product_can_have_any_type(self):
        """Productos sin rol de entintado no se ven afectados por la restricción."""
        service = self.templates.create({
            'name': 'Servicio de Instalación',
            'type': 'service',
        })
        self.assertEqual(service.type, 'service')
        self.assertFalse(service.tint_role)

        consumable = self.templates.create({
            'name': 'Cinta de Enmascarar',
            'type': 'consu',
            'is_storable': False,
        })
        self.assertEqual(consumable.type, 'consu')
        self.assertFalse(consumable.is_storable)
        self.assertFalse(consumable.tint_role)
