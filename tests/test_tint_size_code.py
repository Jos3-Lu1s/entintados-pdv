# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTintSizeCode(TransactionCase):
    """ Normalización del código de presentación: solo trim (sin cambiar
    mayúsculas/minúsculas).

    Se usan códigos fuera del juego precargado (L, G, Q) para que la
    restricción de unicidad de `code` no interfiera con lo que aquí se mide.
    """

    def test_size_code_trimmed_on_create(self):
        size = self.env['tint.size'].create({
            'name': 'Presentación de prueba (create)',
            'code': '  z  ',
            'volume_liters': 0.5,
        })
        self.assertEqual(size.code, 'z')

    def test_size_code_trimmed_on_write(self):
        size = self.env['tint.size'].create({
            'name': 'Presentación de prueba (write)',
            'code': 'X',
            'volume_liters': 0.5,
        })
        size.write({'code': '  y  '})
        self.assertEqual(size.code, 'y')

    def test_size_code_trimmed_onchange(self):
        size = self.env['tint.size'].new({'code': '  z  '})
        size._onchange_code_trim()
        self.assertEqual(size.code, 'z')
