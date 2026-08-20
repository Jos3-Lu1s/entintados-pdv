# -*- coding: utf-8 -*-

from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestTintCapacity(TransactionCase):
    """ Datos maestros físicos del entintado.

    Verifica que la matriz precargada corresponda exactamente a la tabla
    del fabricante y que la relación `puntos por litro × litros` se cumpla
    en las 21 combinaciones. Esta prueba es la red de seguridad que evita
    que un ajuste de datos introduzca una capacidad silenciosamente
    incorrecta.
    """

    #: Tabla original del fabricante: código de base -> presentación -> puntos.
    MANUFACTURER_TABLE = {
        'W': {'L': 24, 'G': 96, 'Q': 456},
        'M': {'L': 48, 'G': 192, 'Q': 912},
        'D': {'L': 96, 'G': 384, 'Q': 1824},
        'A': {'L': 144, 'G': 576, 'Q': 2736},
        'N': {'L': 144, 'G': 576, 'Q': 2736},
        'Y': {'L': 144, 'G': 576, 'Q': 2736},
        'R': {'L': 144, 'G': 576, 'Q': 2736},
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_types = cls.env['tint.base.type']
        cls.sizes = cls.env['tint.size']
        cls.capacities = cls.env['tint.base.capacity']

    # --- Datos precargados ---------------------------------------------

    def test_seven_base_types_loaded(self):
        codes = set(self.base_types.search([]).mapped('code'))
        self.assertEqual(codes, set(self.MANUFACTURER_TABLE))

    def test_three_sizes_loaded_with_volumes(self):
        sizes = {s.code: s.volume_liters for s in self.sizes.search([])}
        self.assertEqual(sizes, {'L': 1.0, 'G': 4.0, 'Q': 19.0})

    def test_matrix_is_complete(self):
        """Las 21 combinaciones deben existir: una capacidad ausente es un
        hueco que hace fallar la venta en caja."""
        self.assertEqual(self.capacities.search_count([]), 21)

    def test_matrix_matches_manufacturer_table(self):
        for base_code, row in self.MANUFACTURER_TABLE.items():
            base_type = self.base_types.search([('code', '=', base_code)], limit=1)
            self.assertTrue(base_type, "Falta el tipo de base %s" % base_code)
            for size_code, expected in row.items():
                size = self.sizes.search([('code', '=', size_code)], limit=1)
                self.assertEqual(
                    base_type.capacity_for(size), expected,
                    "Capacidad incorrecta para %s / %s" % (base_code, size_code),
                )

    def test_matrix_is_internally_consistent(self):
        """`max_points` debe igualar `puntos por litro × litros` en toda la
        matriz precargada."""
        inconsistent = self.capacities.search([('is_consistent', '=', False)])
        self.assertFalse(
            inconsistent,
            "Capacidades inconsistentes: %s" % inconsistent.mapped('display_name'),
        )

    def test_points_per_liter_scale(self):
        """Los factores entre bases son 1, 2, 4 y 6 respecto de White."""
        per_liter = {
            bt.code: bt.points_per_liter for bt in self.base_types.search([])
        }
        self.assertEqual(per_liter['W'], 24)
        self.assertEqual(per_liter['M'], per_liter['W'] * 2)
        self.assertEqual(per_liter['D'], per_liter['W'] * 4)
        self.assertEqual(per_liter['A'], per_liter['W'] * 6)
        self.assertEqual(per_liter['N'], per_liter['A'])
        self.assertEqual(per_liter['Y'], per_liter['A'])
        self.assertEqual(per_liter['R'], per_liter['A'])

    # --- Regla operativa de extracción previa --------------------------

    def test_line_colors_require_extraction(self):
        """Y y R vienen al 100% y exigen extraer el 10% antes de entintar."""
        for code in ('Y', 'R'):
            base_type = self.base_types.search([('code', '=', code)], limit=1)
            self.assertTrue(base_type.is_line_color)
            self.assertEqual(base_type.fill_percentage, 100.0)
            self.assertTrue(base_type.requires_extraction)
            self.assertEqual(base_type.extraction_percentage, 10.0)
            self.assertTrue(
                base_type.operator_note,
                "La base %s debe llevar la instrucción para el operador" % code,
            )

    def test_non_line_colors_do_not_require_extraction(self):
        for code in ('W', 'M', 'D', 'A', 'N'):
            base_type = self.base_types.search([('code', '=', code)], limit=1)
            self.assertFalse(base_type.requires_extraction)

    def test_fill_percentages(self):
        expected = {'W': 100.0, 'M': 97.0, 'D': 94.0, 'A': 91.0, 'N': 91.0,
                    'Y': 100.0, 'R': 100.0}
        actual = {bt.code: bt.fill_percentage for bt in self.base_types.search([])}
        self.assertEqual(actual, expected)

    def test_extraction_without_percentage_fails(self):
        base_type = self.base_types.search([('code', '=', 'W')], limit=1)
        with self.assertRaises(ValidationError):
            base_type.write({'requires_extraction': True, 'extraction_percentage': 0.0})

    # --- Presentación y consultas --------------------------------------

    def test_capacity_display_notation(self):
        white = self.base_types.search([('code', '=', 'W')], limit=1)
        bucket = self.sizes.search([('code', '=', 'Q')], limit=1)
        liter = self.sizes.search([('code', '=', 'L')], limit=1)
        self.assertEqual(white.capacity_display_for(bucket), '9Y 24')
        self.assertEqual(white.capacity_display_for(liter), '24 Pts.')

    def test_capacity_for_missing_combination_raises(self):
        """Falla explícitamente en lugar de devolver cero en silencio."""
        white = self.base_types.search([('code', '=', 'W')], limit=1)
        new_size = self.sizes.create({
            'name': 'Medio litro', 'code': 'H', 'volume_liters': 0.5,
        })
        with self.assertRaises(UserError):
            white.capacity_for(new_size)
        self.assertEqual(white.capacity_for(new_size, raise_if_missing=False), 0)

    def test_capacity_counts(self):
        white = self.base_types.search([('code', '=', 'W')], limit=1)
        liter = self.sizes.search([('code', '=', 'L')], limit=1)
        self.assertEqual(white.capacity_count, 3)
        self.assertEqual(liter.capacity_count, 7)

    # --- Restricciones de integridad -----------------------------------

    @mute_logger('odoo.sql_db')
    def test_duplicate_capacity_rejected(self):
        white = self.base_types.search([('code', '=', 'W')], limit=1)
        liter = self.sizes.search([('code', '=', 'L')], limit=1)
        with self.assertRaises(IntegrityError):
            self.capacities.create({
                'base_type_id': white.id, 'size_id': liter.id, 'max_points': 100,
            })

    @mute_logger('odoo.sql_db')
    def test_duplicate_base_type_code_rejected(self):
        with self.assertRaises(IntegrityError):
            self.base_types.create({
                'name': 'Otra White', 'code': 'W', 'points_per_liter': 24,
            })

    @mute_logger('odoo.sql_db')
    def test_non_positive_capacity_rejected(self):
        white = self.base_types.search([('code', '=', 'W')], limit=1)
        new_size = self.sizes.create({
            'name': 'Cuarto', 'code': 'C', 'volume_liters': 0.25,
        })
        with self.assertRaises(IntegrityError):
            self.capacities.create({
                'base_type_id': white.id, 'size_id': new_size.id, 'max_points': 0,
            })

    @mute_logger('odoo.sql_db')
    def test_non_positive_volume_rejected(self):
        with self.assertRaises(IntegrityError):
            self.sizes.create({'name': 'Vacío', 'code': 'Z', 'volume_liters': 0.0})

    # --- Unidades de medida --------------------------------------------

    def test_ounce_uom_is_48_points(self):
        point = self.env.ref('entintados_pdv.uom_tint_point')
        ounce = self.env.ref('entintados_pdv.uom_tint_ounce')
        self.assertFalse(point.relative_uom_id, "El punto debe ser unidad de referencia")
        self.assertEqual(point.relative_factor, 1.0)
        self.assertEqual(ounce.relative_uom_id, point)
        self.assertEqual(ounce.relative_factor, 48.0)
        self.assertEqual(ounce._compute_quantity(1, point), 48.0)
        self.assertEqual(point._compute_quantity(48, ounce), 1.0)

    def test_point_precision_supports_integer_doses(self):
        """Los puntos son enteros, así que la precisión decimal global por
        omisión es suficiente y no hace falta modificarla."""
        point = self.env.ref('entintados_pdv.uom_tint_point')
        self.assertFalse(point.is_zero(1.0))
