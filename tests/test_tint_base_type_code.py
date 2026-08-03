# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTintBaseTypeCode(TransactionCase):
    """ Normalización del código de tipo de base: solo trim (sin cambiar
    mayúsculas/minúsculas).

    Se usan códigos fuera del juego precargado (W, M, D, A, N, Y, R) para que
    la restricción de unicidad de `code` no interfiera con lo que aquí se mide.
    """

    def test_base_type_code_trimmed_on_create(self):
        base_type = self.env['tint.base.type'].create({
            'name': 'Base de prueba (create)',
            'code': '  z  ',
            'points_per_liter': 456,
            'fill_percentage': 100.0,
        })
        self.assertEqual(base_type.code, 'z')

    def test_base_type_code_trimmed_on_write(self):
        base_type = self.env['tint.base.type'].create({
            'name': 'Base de prueba (write)',
            'code': 'X',
            'points_per_liter': 456,
            'fill_percentage': 100.0,
        })
        base_type.write({'code': '  y  '})
        self.assertEqual(base_type.code, 'y')

    def test_base_type_code_trimmed_onchange(self):
        base_type = self.env['tint.base.type'].new({'code': '  z  '})
        base_type._onchange_code_trim()
        self.assertEqual(base_type.code, 'z')
