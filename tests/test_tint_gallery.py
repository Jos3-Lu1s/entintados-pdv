# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTintGallery(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.galleries = cls.env['tint.gallery']
        cls.colors = cls.env['tint.color']
        cls.formulas = cls.env['tint.color.formula']
        cls.base_types = cls.env['tint.base.type']
        cls.sizes = cls.env['tint.size']

        cls.white = cls.base_types.search([('code', '=', 'W')], limit=1)
        cls.deep = cls.base_types.search([('code', '=', 'D')], limit=1)
        cls.liter = cls.sizes.search([('code', '=', 'L')], limit=1)
        cls.gallon = cls.sizes.search([('code', '=', 'G')], limit=1)

        point = cls.env.ref('entintados_pdv.uom_tint_point')
        cls.colorant_a = cls.env['product.product'].create({
            'name': 'Colorante galería A', 'uom_id': point.id,
            'tint_role': 'colorant', 'list_price': 2.0,
        })

        cls.gallery_a = cls.galleries.create({'name': 'Galería A', 'code': 'GALA'})
        cls.gallery_b = cls.galleries.create({'name': 'Galería B', 'code': 'GALB'})

        cls.color_1 = cls.colors.create({'name': 'Color 1', 'code': 'COL-001'})
        cls.color_2 = cls.colors.create({'name': 'Color 2', 'code': 'COL-002'})
        cls.color_3 = cls.colors.create({'name': 'Color 3', 'code': 'COL-003'})

        # Color 1 tiene 2 fórmulas en Galería A (litro y galón)
        cls.formulas.create({
            'gallery_id': cls.gallery_a.id,
            'color_id': cls.color_1.id,
            'base_type_id': cls.deep.id,
            'size_id': cls.liter.id,
            'line_ids': [(0, 0, {'colorant_id': cls.colorant_a.id, 'points': 10})],
        })
        cls.formulas.create({
            'gallery_id': cls.gallery_a.id,
            'color_id': cls.color_1.id,
            'base_type_id': cls.deep.id,
            'size_id': cls.gallon.id,
            'line_ids': [(0, 0, {'colorant_id': cls.colorant_a.id, 'points': 40})],
        })

        # Color 2 tiene 1 fórmula en Galería A
        cls.formulas.create({
            'gallery_id': cls.gallery_a.id,
            'color_id': cls.color_2.id,
            'base_type_id': cls.white.id,
            'size_id': cls.liter.id,
            'line_ids': [(0, 0, {'colorant_id': cls.colorant_a.id, 'points': 5})],
        })

        # Color 3 solo tiene fórmula en Galería B
        cls.formulas.create({
            'gallery_id': cls.gallery_b.id,
            'color_id': cls.color_3.id,
            'base_type_id': cls.white.id,
            'size_id': cls.liter.id,
            'line_ids': [(0, 0, {'colorant_id': cls.colorant_a.id, 'points': 8})],
        })

    def test_counts_computation(self):
        # Galería A tiene 3 fórmulas pero solo 2 colores únicos
        self.assertEqual(self.gallery_a.formula_count, 3)
        self.assertEqual(self.gallery_a.color_count, 2)

        # Galería B tiene 1 fórmula y 1 color único
        self.assertEqual(self.gallery_b.formula_count, 1)
        self.assertEqual(self.gallery_b.color_count, 1)

    def test_action_open_formulas(self):
        action = self.gallery_a.action_open_formulas()
        self.assertEqual(action.get('type'), 'ir.actions.act_window')
        self.assertEqual(action.get('res_model'), 'tint.color.formula')
        self.assertEqual(action.get('domain'), [('gallery_id', '=', self.gallery_a.id)])
        self.assertEqual(action.get('context', {}).get('default_gallery_id'), self.gallery_a.id)

        formulas = self.formulas.search(action['domain'])
        self.assertEqual(len(formulas), 3)

    def test_action_open_colors(self):
        action = self.gallery_a.action_open_colors()
        self.assertEqual(action.get('type'), 'ir.actions.act_window')
        self.assertEqual(action.get('res_model'), 'tint.color')
        self.assertEqual(action.get('domain'), [('formula_ids.gallery_id', '=', self.gallery_a.id)])

        colors = self.colors.search(action['domain'])
        self.assertEqual(len(colors), 2)
        self.assertEqual(set(colors.ids), {self.color_1.id, self.color_2.id})
        self.assertNotIn(self.color_3.id, colors.ids)
