# -*- coding: utf-8 -*-

from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTintColorGalleryCode(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.colors = cls.env['tint.color']
        cls.formulas = cls.env['tint.color.formula']
        cls.galleries = cls.env['tint.gallery']

        cls.white = cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        cls.liter = cls.env['tint.size'].search([('code', '=', 'L')], limit=1)
        point = cls.env.ref('entintados_pdv.uom_tint_point')
        cls.colorant = cls.env['product.product'].create({
            'name': 'Colorante código galería', 'uom_id': point.id,
            'tint_role': 'colorant', 'list_price': 2.0,
            'is_storable': True,
        })

    def setUp(self):
        super().setUp()
        # Por prueba: la secuencia de PostgreSQL no se revierte al terminar cada una.
        # Prefijos propios para no chocar con los colores de la base.
        self.gallery_r = self.galleries.create({
            'name': 'Transición prueba', 'code': 'ZTRS', 'code_prefix': 'ZR'})
        self.gallery_h = self.galleries.create({
            'name': 'Históricos prueba', 'code': 'ZHIS', 'code_prefix': 'ZH'})
        self.gallery_plain = self.galleries.create({
            'name': 'Milennium prueba', 'code': 'ZMIL'})

    def _color(self, gallery, **vals):
        return self.colors.create({'name': 'Color prueba', 'gallery_id': gallery.id, **vals})

    def _formula(self, color, **vals):
        return self.formulas.create({
            'color_id': color.id,
            'base_type_id': self.white.id,
            'size_id': self.liter.id,
            'line_ids': [(0, 0, {'colorant_id': self.colorant.id, 'points': 5})],
            **vals,
        })

    # --- Galería ----------------------------------------------------------

    def test_gallery_creates_sequence(self):
        sequence = self.gallery_r.color_sequence_id
        self.assertTrue(sequence)
        self.assertEqual(sequence.padding, 4)
        self.assertEqual(sequence.implementation, 'standard')
        self.assertFalse(sequence.prefix)

    def test_data_gallery_prefixes(self):
        expected = {
            'tint_gallery_milennium': False,
            'tint_gallery_transicion': 'R',
            'tint_gallery_historicos': 'H',
            'tint_gallery_tranquility': 'T',
            'tint_gallery_comex': 'C',
            'tint_gallery_berel': 'B',
            'tint_gallery_odoo_manual': 'ODM-',
            'tint_gallery_soi_manual': 'SOI-',
            'tint_gallery_kit_generales': 'KIT-',
        }
        for xmlid, prefix in expected.items():
            gallery = self.env.ref('entintados_pdv.%s' % xmlid)
            self.assertEqual(gallery.code_prefix or False, prefix, xmlid)
            self.assertTrue(gallery.color_sequence_id, xmlid)

    def test_gallery_without_sequence_clear_error(self):
        self.gallery_r.color_sequence_id = False
        with self.assertRaisesRegex(UserError, 'Transición prueba'):
            self._color(self.gallery_r)
        color = self._color(self.gallery_r, code_number='45')
        self.assertEqual(color.code, 'ZR0045')

    # --- Creación ---------------------------------------------------------

    def test_gallery_required(self):
        with self.assertRaises(ValidationError):
            self.colors.create({'name': 'Sin galería'})

    def test_sequence_assigns_prefix_and_padding(self):
        color = self._color(self.gallery_r)
        self.assertEqual(color.code, 'ZR0001')
        self.assertEqual(color.code_number, '0001')

    def test_sequence_skips_occupied(self):
        self._color(self.gallery_r, code='ZR0001')
        self._color(self.gallery_r, code='ZR0002', active=False)
        color = self._color(self.gallery_r)
        self.assertEqual(color.code, 'ZR0003')

    def test_manual_number_padded(self):
        color = self._color(self.gallery_r, code_number='45')
        self.assertEqual(color.code, 'ZR0045')
        self.assertEqual(color.code_number, '0045')

    def test_manual_number_digits_only(self):
        for number in ('A45', 'R45', '4 5'):
            with self.assertRaises(ValidationError, msg=number):
                self._color(self.gallery_r, code_number=number)

    def test_manual_number_duplicate_rejected(self):
        self.colors.create({
            'name': 'Azul ocupado', 'gallery_id': self.gallery_r.id, 'code_number': '45'})
        with self.assertRaisesRegex(ValidationError, 'Azul ocupado'):
            self._color(self.gallery_r, code_number='0045')
        color = self._color(self.gallery_r)
        self.assertEqual(color.code, 'ZR0001')

    def test_same_number_different_gallery(self):
        red = self._color(self.gallery_r, code_number='45')
        hist = self._color(self.gallery_h, code_number='45')
        self.assertEqual(red.code, 'ZR0045')
        self.assertEqual(hist.code, 'ZH0045')

    def test_empty_prefix(self):
        color = self._color(self.gallery_plain, code_number='7592')
        self.assertEqual(color.code, '7592')
        self.assertEqual(color.code_number, '7592')

    def test_grows_beyond_four_digits(self):
        self.gallery_r.color_number_next = 9999
        first = self._color(self.gallery_r)
        second = self._color(self.gallery_r)
        self.assertEqual(first.code, 'ZR9999')
        self.assertEqual(second.code, 'ZR10000')
        self.assertEqual(second.code_number, '10000')

    def test_explicit_code_respected(self):
        matching = self._color(self.gallery_r, code='zr4512')
        self.assertEqual(matching.code, 'ZR4512')
        self.assertEqual(matching.code_number, '4512')
        foreign = self._color(self.gallery_r, code='AC073')
        self.assertEqual(foreign.code, 'AC073')
        self.assertFalse(foreign.code_number)

    # --- Edición ----------------------------------------------------------

    def test_edit_number_recomputes(self):
        color = self._color(self.gallery_r, code_number='45')
        color.code_number = '46'
        self.assertEqual(color.code, 'ZR0046')
        self.assertEqual(color.code_number, '0046')

        self._color(self.gallery_r, code_number='47')
        with self.assertRaises(ValidationError):
            color.code_number = '47'

        color.code_number = False
        self.assertEqual(color.code, 'ZR0001')

    def test_change_gallery_takes_next_sequence(self):
        color = self._color(self.gallery_r, code_number='46')
        formula = self._formula(color)
        self.gallery_h.color_number_next = 150

        color.gallery_id = self.gallery_h
        self.assertEqual(color.code, 'ZH0150')
        self.assertEqual(color.code_number, '0150')
        self.assertEqual(formula.gallery_id, self.gallery_h)

    def test_change_gallery_and_number_together(self):
        color = self._color(self.gallery_r, code_number='46')
        color.write({'gallery_id': self.gallery_h.id, 'code_number': '77'})
        self.assertEqual(color.code, 'ZH0077')

    def test_explicit_code_on_write_respected(self):
        color = self._color(self.gallery_r)
        color.code = 'EAF194'
        self.assertEqual(color.code, 'EAF194')
        self.assertFalse(color.code_number)

    def test_prefix_change_only_new(self):
        old = self._color(self.gallery_r)
        self.gallery_r.code_prefix = 'ZT'
        self.assertEqual(old.code, 'ZR0001')
        new = self._color(self.gallery_r)
        self.assertEqual(new.code, 'ZT0002')

    # --- Formulario -------------------------------------------------------

    def test_code_matches_gallery(self):
        ruled = self._color(self.gallery_r, code_number='45')
        imported = self._color(self.gallery_r, code='AC073')
        self.assertTrue(ruled.code_matches_gallery)
        self.assertFalse(imported.code_matches_gallery)
        self.gallery_r.code_prefix = 'ZT'
        ruled.invalidate_recordset(['code_matches_gallery'])
        self.assertFalse(ruled.code_matches_gallery)

    def test_form_gallery_change_clears_number(self):
        color = self._color(self.gallery_r, code_number='46')
        form = Form(color)
        form.gallery_id = self.gallery_h
        self.assertFalse(form.code_number)
        form.gallery_id = self.gallery_r
        self.assertEqual(form.code_number, '0046')
        form.gallery_id = self.gallery_h
        form.save()
        self.assertEqual(color.code, 'ZH0001')

    # --- Fórmula ----------------------------------------------------------

    def test_formula_gallery_follows_color(self):
        color = self._color(self.gallery_r)
        formula = self._formula(color, gallery_id=self.gallery_h.id)
        self.assertEqual(formula.gallery_id, self.gallery_r)
        formula.write({'gallery_id': self.gallery_h.id})
        self.assertEqual(formula.gallery_id, self.gallery_r)
