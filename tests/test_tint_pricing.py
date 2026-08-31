# -*- coding: utf-8 -*-

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTintPricing(TransactionCase):
    """Pruebas para el costeo de mínimos, máximos y carga de presentaciones de línea."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.templates = cls.env['product.template']
        cls.products = cls.env['product.product']
        cls.base_types = cls.env['tint.base.type']
        cls.sizes = cls.env['tint.size']
        cls.schemas = cls.env['tint.schema']
        cls.lines = cls.env['lines.product']
        cls.presentations = cls.env['lines.product.presentation']
        cls.galleries = cls.env['tint.gallery']
        cls.colors = cls.env['tint.color']
        cls.formulas = cls.env['tint.color.formula']
        cls.formula_lines = cls.env['tint.color.formula.line']
        cls.point = cls.env.ref('entintados_pdv.uom_tint_point')

        cls.white = cls.base_types.search([('code', '=', 'W')], limit=1)
        cls.gallon = cls.sizes.search([('code', '=', 'G')], limit=1)
        cls.bucket = cls.sizes.search([('code', '=', 'Q')], limit=1)

        # Galería y color de prueba
        cls.gallery = cls.galleries.create({
            'name': 'Galería de Prueba',
            'code': 'TST',
        })
        cls.color = cls.colors.create({
            'name': 'Azul de Prueba',
            'code': 'AZ-99',
        })

        # Esquema y línea de producto
        cls.schema = cls.schemas.create({
            'name': 'Esquema Premium',
        })
        cls.line = cls.lines.create({
            'name': 'Línea Vinílica',
            'scheme': cls.schema.id,
        })

        # Presentación con rango: Mínimo $400, Máximo $600
        cls.presentation_gallon = cls.presentations.create({
            'line_id': cls.line.id,
            'presentation_id': cls.gallon.id,
            'price_min': 400.0,
            'price_max': 600.0,
        })

        # Colorante con costo y precio
        cls.colorant_tmpl = cls.templates.create({
            'name': 'Colorante Negro TST',
            'tint_role': 'colorant',
            'uom_id': cls.point.id,
            'list_price': 2.0,
            'standard_price': 1.0,
        })
        cls.colorant = cls.colorant_tmpl.product_variant_id

    def _create_base_product(self, base_type, size, list_price=300.0, line=None):
        tmpl = self.templates.create({
            'name': f'Base {base_type.code} {size.code}',
            'tint_role': 'base',
            'tint_base_type_id': base_type.id,
            'tint_size_id': size.id,
            'lines_product_id': line.id if line else False,
            'list_price': list_price,
        })
        return tmpl.product_variant_id

    def _create_formula(self, base_type, size, points=10):
        formula = self.formulas.create({
            'gallery_id': self.gallery.id,
            'color_id': self.color.id,
            'base_type_id': base_type.id,
            'size_id': size.id,
        })
        self.formula_lines.create({
            'formula_id': formula.id,
            'colorant_id': self.colorant.id,
            'points': points,
            'sequence': 10,
        })
        return formula

    # --- Validaciones del modelo de presentación ------------------------

    def test_price_min_greater_or_equal_to_max_fails(self):
        """El precio mínimo no puede ser mayor o igual que el máximo."""
        with self.assertRaises(ValidationError):
            self.presentations.create({
                'line_id': self.line.id,
                'presentation_id': self.bucket.id,
                'price_min': 500.0,
                'price_max': 500.0,
            })

    def test_unique_line_presentation_constraint(self):
        """No se puede duplicar la misma presentación en una línea."""
        with self.assertRaises(Exception):
            self.presentations.create({
                'line_id': self.line.id,
                'presentation_id': self.gallon.id,
                'price_min': 300.0,
                'price_max': 500.0,
            })

    # --- Carga de campos al POS -----------------------------------------

    def test_pos_data_fields_registered(self):
        """Verifica que los modelos expongan sus campos requeridos para POS."""
        pres_fields = self.presentations._load_pos_data_fields(False)
        self.assertIn('line_id', pres_fields)
        self.assertIn('presentation_id', pres_fields)
        self.assertIn('price_min', pres_fields)
        self.assertIn('price_max', pres_fields)

        tmpl_fields = self.templates._load_pos_data_fields(False)
        self.assertIn('lines_product_id', tmpl_fields)
        self.assertIn('scheme_id', tmpl_fields)

    # --- Lógica de Costeo y Rango en Fórmulas ---------------------------

    def test_formula_cost_min_and_cost_max(self):
        """Verifica el cálculo de costo (standard_price) y precio (list_price) en la fórmula."""
        formula = self._create_formula(self.white, self.gallon, points=20)
        # 20 pts * standard_price ($1.0) = $20.0
        self.assertEqual(formula.cost_min, 20.0)
        # 20 pts * list_price ($2.0) = $40.0
        self.assertEqual(formula.cost_max, 40.0)

    def test_presentation_price_osel_computed(self):
        """Verifica que price_osel se sincronice con price_min."""
        pres = self.presentations.create({
            'line_id': self.line.id,
            'presentation_id': self.bucket.id,
            'price_min': 1500.0,
            'price_max': 2000.0,
        })
        self.assertEqual(pres.price_osel, 1500.0)

    def test_base_product_linked_to_line_and_scheme(self):
        """Verifica que el producto base conserve su línea y esquema relacionado."""
        base = self._create_base_product(self.white, self.gallon, line=self.line)
        self.assertEqual(base.lines_product_id, self.line)
        self.assertEqual(base.scheme_id, self.schema)

    # --- Validación de Modelos en Sesión POS y Lógica de Rango ---------

    def test_pos_session_loads_pricing_models(self):
        """Verifica que la sesión del POS incluya los modelos de esquema, líneas y presentaciones."""
        pos_config = self.env['pos.config'].create({'name': 'Test POS'})
        models_to_load = self.env['pos.session']._load_pos_data_models(pos_config)
        self.assertIn('tint.schema', models_to_load)
        self.assertIn('lines.product', models_to_load)
        self.assertIn('lines.product.presentation', models_to_load)

    def test_price_clamping_scenarios(self):
        """Valida matemáticamente las 3 condiciones de acotamiento de precio según el rango configurado."""
        # Rango configurado: Min $400.0, Max $600.0
        base = self._create_base_product(self.white, self.gallon, list_price=300.0, line=self.line)
        pres = self.presentation_gallon

        # Escenario 1: Tinte ligero (Teórico $300 + $40 = $340 < $400) -> Acota a $400.0
        formula_low = self._create_formula(self.white, self.gallon, points=20)
        colorant_price_low = sum(line.colorant_id.list_price * line.points for line in formula_low.line_ids)
        theoretical_low = base.list_price + colorant_price_low
        self.assertEqual(theoretical_low, 340.0)

        clamped_low = pres.price_min if theoretical_low < pres.price_min else (
            pres.price_max if theoretical_low > pres.price_max else theoretical_low
        )
        self.assertEqual(clamped_low, 400.0)

        # Escenario 2: Tinte medio (Teórico $300 + $150 = $450 en [$400, $600]) -> Queda en $450.0
        formula_mid = self._create_formula(self.white, self.gallon, points=75)
        colorant_price_mid = sum(line.colorant_id.list_price * line.points for line in formula_mid.line_ids)
        theoretical_mid = base.list_price + colorant_price_mid
        self.assertEqual(theoretical_mid, 450.0)

        clamped_mid = pres.price_min if theoretical_mid < pres.price_min else (
            pres.price_max if theoretical_mid > pres.price_max else theoretical_mid
        )
        self.assertEqual(clamped_mid, 450.0)

        # Escenario 3: Tinte saturado (Teórico $300 + $400 = $700 > $600) -> Acota a $600.0
        formula_high = self._create_formula(self.white, self.gallon, points=200)
        colorant_price_high = sum(line.colorant_id.list_price * line.points for line in formula_high.line_ids)
        theoretical_high = base.list_price + colorant_price_high
        self.assertEqual(theoretical_high, 700.0)

        clamped_high = pres.price_min if theoretical_high < pres.price_min else (
            pres.price_max if theoretical_high > pres.price_max else theoretical_high
        )
        self.assertEqual(clamped_high, 600.0)

