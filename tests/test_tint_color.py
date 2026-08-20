# -*- coding: utf-8 -*-

from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestTintColor(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.colors = cls.env['tint.color']
        cls.formulas = cls.env['tint.color.formula']
        cls.base_types = cls.env['tint.base.type']
        cls.sizes = cls.env['tint.size']

        cls.white = cls.base_types.search([('code', '=', 'W')], limit=1)
        cls.deep = cls.base_types.search([('code', '=', 'D')], limit=1)
        cls.yellow = cls.base_types.search([('code', '=', 'Y')], limit=1)
        cls.liter = cls.sizes.search([('code', '=', 'L')], limit=1)
        cls.gallon = cls.sizes.search([('code', '=', 'G')], limit=1)
        cls.bucket = cls.sizes.search([('code', '=', 'Q')], limit=1)

        point = cls.env.ref('entintados_pdv.uom_tint_point')
        cls.colorant_a = cls.env['product.product'].create({
            'name': 'Colorante prueba A', 'uom_id': point.id,
            'tint_role': 'colorant', 'price_per_point': 2.0,
        })
        cls.colorant_b = cls.env['product.product'].create({
            'name': 'Colorante prueba B', 'uom_id': point.id,
            'tint_role': 'colorant', 'price_per_point': 3.0,
        })
        cls.color = cls.colors.create({'name': 'Color de prueba'})

    def _create_formula(self, base_type, size, doses, color=None):
        return self.formulas.create({
            'color_id': (color or self.color).id,
            'base_type_id': base_type.id,
            'size_id': size.id,
            'line_ids': [
                (0, 0, {'colorant_id': colorant.id, 'points': points})
                for colorant, points in doses
            ],
        })

    # --- Color ----------------------------------------------------------

    def test_color_code_generated_automatically(self):
        color = self.colors.create({'name': 'Verde Olivo'})
        self.assertTrue(color.code, "El código debe generarse desde la secuencia")

    def test_color_code_respected_when_given(self):
        color = self.colors.create({'name': 'Gris Perla', 'code': 'MIO-001'})
        self.assertEqual(color.code, 'MIO-001')

    def test_invalid_hex_color_rejected(self):
        with self.assertRaises(ValidationError):
            self.colors.create({'name': 'Color raro', 'html_color': 'rojo'})

    def test_valid_hex_color_accepted(self):
        for value in ('#C8102E', '#fff'):
            color = self.colors.create({'name': 'Color %s' % value, 'html_color': value})
            self.assertEqual(color.html_color, value)

    @mute_logger('odoo.sql_db')
    def test_duplicate_color_code_rejected(self):
        self.colors.create({'name': 'Primero', 'code': 'DUP-1'})
        with self.assertRaises(IntegrityError):
            self.colors.create({'name': 'Segundo', 'code': 'DUP-1'})

    # --- Total y capacidad ---------------------------------------------

    def test_total_and_capacity(self):
        formula = self._create_formula(
            self.deep, self.gallon, [(self.colorant_a, 40), (self.colorant_b, 8)])
        self.assertEqual(formula.total_points, 48)
        self.assertEqual(formula.total_points_display, '1Y')
        self.assertEqual(formula.capacity_points, 384)
        self.assertEqual(formula.capacity_display, '8Y')
        self.assertEqual(formula.remaining_points, 336)
        self.assertTrue(formula.fits)

    def test_formula_exceeding_capacity_rejected(self):
        """Una fórmula que no cabe se derramaría al dispensar."""
        with self.assertRaises(ValidationError):
            self._create_formula(self.white, self.liter, [(self.colorant_a, 25)])

    def test_formula_exactly_at_capacity_accepted(self):
        formula = self._create_formula(self.white, self.liter, [(self.colorant_a, 24)])
        self.assertEqual(formula.remaining_points, 0)
        self.assertTrue(formula.fits)

    def test_adding_dose_beyond_capacity_rejected(self):
        formula = self._create_formula(self.white, self.liter, [(self.colorant_a, 20)])
        with self.assertRaises(ValidationError):
            formula.write({
                'line_ids': [(0, 0, {'colorant_id': self.colorant_b.id, 'points': 10})]
            })

    # --- Restricciones de integridad -----------------------------------

    @mute_logger('odoo.sql_db')
    def test_duplicate_formula_rejected(self):
        self._create_formula(self.deep, self.gallon, [(self.colorant_a, 10)])
        with self.assertRaises(IntegrityError):
            self._create_formula(self.deep, self.gallon, [(self.colorant_b, 10)])

    @mute_logger('odoo.sql_db')
    def test_same_colorant_twice_rejected(self):
        with self.assertRaises(IntegrityError):
            self._create_formula(
                self.deep, self.gallon, [(self.colorant_a, 10), (self.colorant_a, 5)])

    @mute_logger('odoo.sql_db')
    def test_zero_dose_rejected(self):
        with self.assertRaises(IntegrityError):
            self._create_formula(self.deep, self.gallon, [(self.colorant_a, 0)])

    def test_non_colorant_product_rejected(self):
        base = self.env['product.product'].create({
            'name': 'Base como ingrediente',
            'tint_role': 'base',
            'tint_base_type_id': self.white.id,
            'tint_size_id': self.liter.id,
        })
        with self.assertRaises(ValidationError):
            self._create_formula(self.deep, self.gallon, [(base, 10)])

    # --- Alternativas por base -----------------------------------------

    def test_color_can_have_formulas_on_several_bases(self):
        self._create_formula(self.deep, self.gallon, [(self.colorant_a, 40)])
        self._create_formula(self.white, self.gallon, [(self.colorant_a, 20)])
        self.assertEqual(self.color.formula_count, 2)
        self.assertEqual(len(self.color.formulas_for_size(self.gallon)), 2)
        self.assertEqual(
            set(self.color.base_type_ids.ids), {self.deep.id, self.white.id})

    def test_formula_for_lookup(self):
        formula = self._create_formula(self.deep, self.gallon, [(self.colorant_a, 40)])
        self.assertEqual(self.color.formula_for(self.deep, self.gallon), formula)
        self.assertFalse(self.color.formula_for(self.deep, self.liter))

    # --- Escalado a otras presentaciones -------------------------------

    def test_generate_other_sizes_scales_doses(self):
        """Desde litro: galón multiplica por 4 y cubeta por 19."""
        formula = self._create_formula(self.deep, self.liter, [(self.colorant_a, 10)])
        formula.action_generate_other_sizes()
        self.assertEqual(self.color.formula_count, 3)
        gallon = self.color.formula_for(self.deep, self.gallon)
        bucket = self.color.formula_for(self.deep, self.bucket)
        self.assertEqual(gallon.total_points, 40)
        self.assertEqual(bucket.total_points, 190)

    def test_generate_other_sizes_does_not_overwrite(self):
        """Lo ya capturado a mano se respeta."""
        formula = self._create_formula(self.deep, self.liter, [(self.colorant_a, 10)])
        manual = self._create_formula(self.deep, self.gallon, [(self.colorant_a, 33)])
        formula.action_generate_other_sizes()
        self.assertEqual(manual.total_points, 33, "No debió sobreescribirse")

    def test_generate_other_sizes_without_lines_fails(self):
        formula = self.formulas.create({
            'color_id': self.color.id,
            'base_type_id': self.deep.id,
            'size_id': self.liter.id,
        })
        with self.assertRaises(UserError):
            formula.action_generate_other_sizes()

    def test_generate_other_sizes_twice_fails(self):
        formula = self._create_formula(self.deep, self.liter, [(self.colorant_a, 10)])
        formula.action_generate_other_sizes()
        with self.assertRaises(UserError):
            formula.action_generate_other_sizes()

    # --- Instrucción operativa heredada --------------------------------

    def test_formula_carries_extraction_instruction(self):
        formula = self._create_formula(self.yellow, self.gallon, [(self.colorant_a, 30)])
        self.assertTrue(formula.requires_extraction)
        self.assertTrue(formula.operator_note)
