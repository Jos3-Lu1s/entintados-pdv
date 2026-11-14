# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, tagged

from ..utils.origin_badge import (
    COMBO_ORIGIN_PREFIX,
    FIXED_PRICE_LABEL,
    format_discount_percent,
    format_origin_badge,
)


@tagged('post_install', '-at_install')
class TestOriginBadge(TransactionCase):

    def test_discount_percent_without_trailing_zeros(self):
        cases = [
            (30.0, '30%'),
            (12.5, '12.5%'),
            (12.25, '12.25%'),
            (33.333, '33.33%'),
            (0.0, '0%'),
            (100, '100%'),
            (9.999, '10%'),
        ]
        for discount, expected in cases:
            with self.subTest(discount=discount):
                self.assertEqual(format_discount_percent(discount), expected)

    def test_badge_with_discount(self):
        cases = [
            (('Buen fin', 30.0), 'Buen fin · 30%'),
            (('Buen fin', 12.5), 'Buen fin · 12.5%'),
            (('Buen fin', 33.333), 'Buen fin · 33.33%'),
            (('Producto', 10.0), 'Producto · 10%'),
            (('Línea Vinílicas', 5.0), 'Línea Vinílicas · 5%'),
            (('Esquema Mayoreo', 8.0), 'Esquema Mayoreo · 8%'),
            (('Cliente', 3.0), 'Cliente · 3%'),
            (('Manual', 15.0), 'Manual · 15%'),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(format_origin_badge(*args), expected)

    def test_badge_with_text_term(self):
        self.assertEqual(format_origin_badge('Buen fin', 'Regalo'), 'Buen fin · Regalo')

    def test_badge_without_discount(self):
        self.assertEqual(format_origin_badge('Buen fin'), 'Buen fin')
        self.assertEqual(FIXED_PRICE_LABEL, 'Precio fijo')

    def test_badge_with_empty_name_keeps_type(self):
        self.assertEqual(format_origin_badge('Línea ', 5.0), 'Línea · 5%')
        self.assertEqual(format_origin_badge('', 5.0), '5%')

    def test_combo_prefix(self):
        self.assertEqual(COMBO_ORIGIN_PREFIX + 'Buen fin · 50%', '↳ Buen fin · 50%')
