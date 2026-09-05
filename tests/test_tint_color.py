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
        cls.galleries = cls.env['tint.gallery']

        cls.gallery = cls.galleries.create({'name': 'Galería de prueba', 'code': 'TST-GAL'})
        cls.white = cls.base_types.search([('code', '=', 'W')], limit=1)
        cls.deep = cls.base_types.search([('code', '=', 'D')], limit=1)
        cls.yellow = cls.base_types.search([('code', '=', 'Y')], limit=1)
        cls.liter = cls.sizes.search([('code', '=', 'L')], limit=1)
        cls.gallon = cls.sizes.search([('code', '=', 'G')], limit=1)
        cls.bucket = cls.sizes.search([('code', '=', 'Q')], limit=1)

        point = cls.env.ref('entintados_pdv.uom_tint_point')
        cls.colorant_a = cls.env['product.product'].create({
            'name': 'Colorante prueba A', 'uom_id': point.id,
            'tint_role': 'colorant', 'list_price': 2.0,
        })
        cls.colorant_b = cls.env['product.product'].create({
            'name': 'Colorante prueba B', 'uom_id': point.id,
            'tint_role': 'colorant', 'list_price': 3.0,
        })
        cls.color = cls.colors.create({'name': 'Color de prueba', 'code': 'TEST-COL-01'})

    def _create_formula(self, base_type, size, doses, color=None, gallery=None):
        return self.formulas.create({
            'gallery_id': (gallery or self.gallery).id,
            'color_id': (color or self.color).id,
            'base_type_id': base_type.id,
            'size_id': size.id,
            'line_ids': [
                (0, 0, {'colorant_id': colorant.id, 'points': points})
                for colorant, points in doses
            ],
        })

    # --- Color ----------------------------------------------------------

    @mute_logger('odoo.sql_db')
    def test_color_code_required(self):
        with self.assertRaises(Exception):
            self.colors.create({'name': 'Verde Olivo'})

    def test_color_code_normalized(self):
        color = self.colors.create({'name': 'Gris Perla', 'code': '  mio-001  '})
        self.assertEqual(color.code, 'MIO-001')

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

    def test_formula_with_decimal_doses(self):
        formula = self._create_formula(
            self.deep, self.gallon, [(self.colorant_a, 24.5), (self.colorant_b, 0.5)])
        self.assertEqual(formula.total_points, 25.0)
        self.assertEqual(formula.total_points_display, '25 Pts.')
        self.assertEqual(formula.remaining_points, 359.0)
        self.assertTrue(formula.fits)
        line_a = formula.line_ids.filtered(lambda l: l.colorant_id == self.colorant_a)
        self.assertEqual(line_a.points_display, '24.5 Pts.')

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
            'gallery_id': self.gallery.id,
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

    # --- Colorantes universales e independencia de línea ---------------

    def test_universal_colorants_shared_across_lines(self):
        """Los colorantes universales (sin línea) pueden usarse en fórmulas
        con diferentes líneas comerciales o sin línea."""
        schema1 = self.env['tint.schema'].create({'name': 'Esquema Vinílico'})
        schema2 = self.env['tint.schema'].create({'name': 'Esquema Esmalte'})
        line1 = self.env['lines.product'].create({'name': 'Línea Premium', 'scheme': schema1.id})
        line2 = self.env['lines.product'].create({'name': 'Línea Estándar', 'scheme': schema2.id})

        self.assertFalse(self.colorant_a.product_tmpl_id.lines_product_id)
        self.assertFalse(self.colorant_b.product_tmpl_id.lines_product_id)

        color_uno = self.colors.create({'name': 'Color Uno', 'code': 'TEST-COL-01-UNI'})
        color_dos = self.colors.create({'name': 'Color Dos', 'code': 'TEST-COL-02-UNI'})

        f1 = self.formulas.create({
            'gallery_id': self.gallery.id,
            'color_id': color_uno.id,
            'base_type_id': self.white.id,
            'size_id': self.liter.id,
            'line_scheme_id': line1.id,
            'line_ids': [
                (0, 0, {'colorant_id': self.colorant_a.id, 'points': 12.0}),
                (0, 0, {'colorant_id': self.colorant_b.id, 'points': 6.0}),
            ],
        })
        self.assertEqual(f1.total_points, 18.0)
        self.assertEqual(f1.line_ids[0].colorant_id, self.colorant_a)

        f2 = self.formulas.create({
            'gallery_id': self.gallery.id,
            'color_id': color_dos.id,
            'base_type_id': self.white.id,
            'size_id': self.liter.id,
            'line_scheme_id': line2.id,
            'line_ids': [
                (0, 0, {'colorant_id': self.colorant_a.id, 'points': 8.0}),
            ],
        })
        self.assertEqual(f2.total_points, 8.0)
        self.assertEqual(f2.line_ids[0].colorant_id, self.colorant_a)

        f1.action_generate_other_sizes()
        gallon_f1 = color_uno.formula_for(self.white, self.gallon, gallery=self.gallery)
        self.assertIsNotNone(gallon_f1)
        self.assertEqual(gallon_f1.total_points, 72.0)

    # --- Generación de otras presentaciones: robustez y consistencia ----

    def test_generate_other_sizes_propagates_line_scheme(self):
        """Comprobar que las fórmulas derivadas conservan line_scheme_id y su scheme_id."""
        schema = self.env['tint.schema'].create({'name': 'Esquema Propagación'})
        line = self.env['lines.product'].create({'name': 'Línea Propagación', 'scheme': schema.id})
        color_prop = self.colors.create({'name': 'Color Propagación', 'code': 'TEST-COL-PROP'})

        formula = self.formulas.create({
            'gallery_id': self.gallery.id,
            'color_id': color_prop.id,
            'base_type_id': self.deep.id,
            'size_id': self.liter.id,
            'line_scheme_id': line.id,
            'line_ids': [
                (0, 0, {'colorant_id': self.colorant_a.id, 'points': 10.0}),
            ],
        })
        formula.action_generate_other_sizes()
        gallon = color_prop.formula_for(self.deep, self.gallon, gallery=self.gallery)
        bucket = color_prop.formula_for(self.deep, self.bucket, gallery=self.gallery)
        self.assertTrue(gallon)
        self.assertTrue(bucket)
        self.assertEqual(gallon.line_scheme_id, line)
        self.assertEqual(gallon.scheme_id, schema)
        self.assertEqual(bucket.line_scheme_id, line)
        self.assertEqual(bucket.scheme_id, schema)

    def test_generate_other_sizes_reactivates_archived_formula(self):
        """Comprobar que una fórmula archivada previa se reactiva y actualiza sin colisión única."""
        schema = self.env['tint.schema'].create({'name': 'Esquema Reactivación'})
        line = self.env['lines.product'].create({'name': 'Línea Reactivación', 'scheme': schema.id})
        color_test = self.colors.create({'name': 'Color Reactivación', 'code': 'TEST-REACT-01'})

        archived_gallon = self.formulas.create({
            'gallery_id': self.gallery.id,
            'color_id': color_test.id,
            'base_type_id': self.deep.id,
            'size_id': self.gallon.id,
            'active': False,
            'line_ids': [
                (0, 0, {'colorant_id': self.colorant_b.id, 'points': 5.0}),
            ],
        })
        self.assertFalse(archived_gallon.active)

        origin = self.formulas.create({
            'gallery_id': self.gallery.id,
            'color_id': color_test.id,
            'base_type_id': self.deep.id,
            'size_id': self.liter.id,
            'line_scheme_id': line.id,
            'line_ids': [
                (0, 0, {'colorant_id': self.colorant_a.id, 'points': 10.0}),
            ],
        })

        origin.action_generate_other_sizes()

        self.assertTrue(archived_gallon.active)
        self.assertEqual(archived_gallon.line_scheme_id, line)
        self.assertEqual(archived_gallon.scheme_id, schema)
        self.assertEqual(archived_gallon.total_points, 40.0)
        self.assertEqual(len(archived_gallon.line_ids), 1)
        self.assertEqual(archived_gallon.line_ids.colorant_id, self.colorant_a)

    def test_generate_other_sizes_omits_capacity_exceeded_partially(self):
        """Comprobar que presentaciones que exceden la capacidad son omitidas sin abortar las que caben."""
        color_test = self.colors.create({'name': 'Color Capacidad Parcial', 'code': 'TEST-CAP-01'})
        base_test = self.base_types.create({
            'name': 'Base Capacidad Test',
            'code': 'BCT',
            'points_per_liter': 50,
        })
        self.env['tint.base.capacity'].create([
            {'base_type_id': base_test.id, 'size_id': self.liter.id, 'max_points': 50},
            {'base_type_id': base_test.id, 'size_id': self.gallon.id, 'max_points': 100},
            {'base_type_id': base_test.id, 'size_id': self.bucket.id, 'max_points': 100},
        ])

        origin = self.formulas.create({
            'gallery_id': self.gallery.id,
            'color_id': color_test.id,
            'base_type_id': base_test.id,
            'size_id': self.liter.id,
            'line_ids': [
                (0, 0, {'colorant_id': self.colorant_a.id, 'points': 20.0}),
            ],
        })

        res = origin.action_generate_other_sizes()

        gallon = color_test.formula_for(base_test, self.gallon, gallery=self.gallery)
        self.assertTrue(gallon)
        self.assertEqual(gallon.total_points, 80.0)

        bucket = color_test.formula_for(base_test, self.bucket, gallery=self.gallery)
        self.assertFalse(bucket)

        self.assertEqual(res.get('type'), 'ir.actions.client')
        self.assertEqual(res.get('tag'), 'display_notification')
        self.assertEqual(res['params']['type'], 'warning')
        self.assertIn(self.bucket.display_name, res['params']['message'])
        self.assertIn('next', res['params'])
        self.assertEqual(res['params']['next']['res_model'], 'tint.color.formula')
        self.assertEqual(res['params']['next']['domain'], [('id', 'in', gallon.ids)])
        self.assertEqual(res['params']['next']['views'], [(False, 'list'), (False, 'form')])

    def test_generate_other_sizes_all_capacity_exceeded_raises_user_error(self):
        """Comprobar la respuesta cuando todas las presentaciones pendientes exceden la capacidad."""
        color_test = self.colors.create({'name': 'Color Exceso Total', 'code': 'TEST-CAP-02'})
        base_test = self.base_types.create({
            'name': 'Base Capacidad Total',
            'code': 'BTT',
            'points_per_liter': 50,
        })
        self.env['tint.base.capacity'].create([
            {'base_type_id': base_test.id, 'size_id': self.liter.id, 'max_points': 50},
            {'base_type_id': base_test.id, 'size_id': self.gallon.id, 'max_points': 50},
            {'base_type_id': base_test.id, 'size_id': self.bucket.id, 'max_points': 50},
        ])

        origin = self.formulas.create({
            'gallery_id': self.gallery.id,
            'color_id': color_test.id,
            'base_type_id': base_test.id,
            'size_id': self.liter.id,
            'line_ids': [
                (0, 0, {'colorant_id': self.colorant_a.id, 'points': 20.0}),
            ],
        })

        with self.assertRaises(UserError) as cm:
            origin.action_generate_other_sizes()

        msg = str(cm.exception)
        self.assertIn(self.gallon.display_name, msg)
        self.assertIn(self.bucket.display_name, msg)

