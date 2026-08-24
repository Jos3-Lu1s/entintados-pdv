# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, tagged

from ..utils.points import (
    POINTS_PER_OUNCE,
    format_points,
    format_points_long,
    parse_points,
    split_points,
    to_points,
)


@tagged('post_install', '-at_install')
class TestTintPoints(TransactionCase):
    """ Notación de puntos y onzas de colorante:

    - Una onza equivale exactamente a 48 puntos.
    - El formato mixto sigue la convención del fabricante: `9Y 24` con
      onzas y resto, `2Y` con onzas exactas y `24 Pts.` sin onzas.
    - El parseo acepta las variantes que el personal escribe en la práctica
      y devuelve `None` en lugar de un cero silencioso cuando no entiende.
    """

    def test_points_per_ounce_is_48(self):
        self.assertEqual(POINTS_PER_OUNCE, 48)
        self.assertEqual(to_points(ounces=1), 48)

    def test_split_points(self):
        self.assertEqual(split_points(456), (9, 24))
        self.assertEqual(split_points(96), (2, 0))
        self.assertEqual(split_points(24), (0, 24))
        self.assertEqual(split_points(24.5), (0, 24.5))
        self.assertEqual(split_points(456.5), (9, 24.5))

    def test_format_matches_manufacturer_table(self):
        """Los valores exactos de la tabla del fabricante."""
        expected = {
            24: '24 Pts.',
            96: '2Y',
            456: '9Y 24',
            48: '1Y',
            192: '4Y',
            912: '19Y',
            384: '8Y',
            1824: '38Y',
            144: '3Y',
            576: '12Y',
            2736: '57Y',
            0.5: '0.5 Pts.',
            24.5: '24.5 Pts.',
            48.5: '1Y 0.5',
            456.75: '9Y 24.75',
        }
        for points, text in expected.items():
            self.assertEqual(format_points(points), text, "Falla el formato de %s puntos" % points)

    def test_format_zero(self):
        self.assertEqual(format_points(0), '0 Pts.')

    def test_format_long(self):
        self.assertEqual(format_points_long(456), '9 Onzas 24 Pts. (456 Pts.)')
        self.assertEqual(format_points_long(48), '1 Onza (48 Pts.)')
        self.assertEqual(format_points_long(24), '24 Pts.')
        self.assertEqual(format_points_long(456.5), '9 Onzas 24.5 Pts. (456.5 Pts.)')
        self.assertEqual(format_points_long(24.5), '24.5 Pts.')
        self.assertEqual(format_points_long(0.5), '0.5 Pts.')

    def test_parse_variants(self):
        for text in ('9Y 24', '9Y24', '9 Y 24', '9 onzas 24 pts', '456', '456 Pts.'):
            self.assertEqual(parse_points(text), 456, "Falla el parseo de %r" % text)
        self.assertEqual(parse_points('2Y'), 96)
        self.assertEqual(parse_points(456), 456)
        self.assertEqual(parse_points('9Y 24.5'), 456.5)
        self.assertEqual(parse_points('0.5 Pts.'), 0.5)
        self.assertEqual(parse_points('456.75'), 456.75)

    def test_parse_invalid_returns_none(self):
        """Devolver None y no cero: el llamador debe poder reportar el error."""
        for text in ('no es un numero', '', '   ', 'abc', None):
            self.assertIsNone(parse_points(text), "Debió rechazar %r" % text)

    def test_round_trip(self):
        for points in (0, 0.25, 0.5, 1, 24, 24.5, 47, 48, 48.5, 49, 96, 144, 456, 456.75, 912, 1824, 2736):
            self.assertEqual(parse_points(format_points(points)), points)
