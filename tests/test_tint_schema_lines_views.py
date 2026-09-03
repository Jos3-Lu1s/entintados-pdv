# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTintSchemaLinesViews(TransactionCase):
    """Pruebas para los modelos y vistas de Esquemas y Líneas de Producto."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schemas = cls.env['tint.schema']
        cls.lines = cls.env['lines.product']
        cls.presentations = cls.env['lines.product.presentation']
        cls.templates = cls.env['product.template']
        cls.base_types = cls.env['tint.base.type']
        cls.sizes = cls.env['tint.size']

        cls.base_w = cls.base_types.search([('code', '=', 'W')], limit=1)
        cls.size_g = cls.sizes.search([('code', '=', 'G')], limit=1)
        cls.size_q = cls.sizes.search([('code', '=', 'Q')], limit=1)

    def test_schema_counts_and_actions(self):
        """Valida que los contadores y acciones de navegación del esquema funcionen."""
        schema = self.schemas.create({
            'name': 'Esquema Decorativo Test',
            'notes': '<p>Notas de prueba</p>',
        })

        self.assertEqual(schema.line_count, 0)
        self.assertEqual(schema.product_count, 0)

        # Crear líneas asociadas
        line1 = self.lines.create({
            'name': 'Línea Satinada',
            'scheme': schema.id,
        })
        line2 = self.lines.create({
            'name': 'Línea Mate',
            'scheme': schema.id,
        })

        schema.invalidate_recordset(['line_count', 'product_count'])
        self.assertEqual(schema.line_count, 2)

        # Crear productos asociados
        prod1 = self.templates.create({
            'name': 'Base Satinada Blanca Galón',
            'tint_role': 'base',
            'tint_base_type_id': self.base_w.id,
            'tint_size_id': self.size_g.id,
            'lines_product_id': line1.id,
            'list_price': 500.0,
            'standard_price': 300.0,
        })

        self.assertEqual(prod1.scheme_id, schema)

        schema.invalidate_recordset(['line_count', 'product_count'])
        self.assertEqual(schema.product_count, 1)

        # Probar action_view_lines
        action_lines = schema.action_view_lines()
        self.assertEqual(action_lines['domain'], [('scheme', '=', schema.id)])
        self.assertEqual(action_lines['context'].get('default_scheme'), schema.id)

        # Probar action_view_products
        action_products = schema.action_view_products()
        self.assertEqual(action_products['domain'], [('scheme_id', '=', schema.id)])
        self.assertEqual(action_products['context'].get('search_default_tint_base'), 1)

    def test_line_counts_and_actions(self):
        """Valida que los contadores y acciones de navegación de las líneas funcionen."""
        schema = self.schemas.create({'name': 'Esquema Industrial Test'})
        line = self.lines.create({
            'name': 'Línea Epóxica',
            'scheme': schema.id,
        })

        self.assertEqual(line.presentation_count, 0)
        self.assertEqual(line.product_count, 0)

        # Agregar presentaciones
        if self.size_g and self.size_q:
            self.presentations.create({
                'line_id': line.id,
                'presentation_id': self.size_g.id,
                'price_min': 600.0,
                'price_max': 900.0,
            })
            self.presentations.create({
                'line_id': line.id,
                'presentation_id': self.size_q.id,
                'price_min': 200.0,
                'price_max': 300.0,
            })

            line.invalidate_recordset(['presentation_count', 'product_count'])
            self.assertEqual(line.presentation_count, 2)

        # Agregar producto a la línea
        self.templates.create({
            'name': 'Base Epóxica Blanca Galón',
            'tint_role': 'base',
            'tint_base_type_id': self.base_w.id,
            'tint_size_id': self.size_g.id,
            'lines_product_id': line.id,
            'list_price': 700.0,
            'standard_price': 400.0,
        })

        line.invalidate_recordset(['presentation_count', 'product_count'])
        self.assertEqual(line.product_count, 1)

        # Probar action_view_products
        action_products = line.action_view_products()
        self.assertEqual(action_products['domain'], [('lines_product_id', '=', line.id)])
        self.assertEqual(action_products['context'].get('default_lines_product_id'), line.id)

    def test_archiving_and_pos_domains(self):
        """Valida el soporte de archivado en esquemas y líneas."""
        schema = self.schemas.create({'name': 'Esquema Descontinuado'})
        line = self.lines.create({'name': 'Línea Antigua', 'scheme': schema.id})

        self.assertTrue(schema.active)
        self.assertTrue(line.active)

        # Archivar
        schema.active = False
        line.active = False

        self.assertFalse(schema.active)
        self.assertFalse(line.active)

        # Verificar dominio POS
        pos_schema_domain = self.schemas._load_pos_data_domain({}, None)
        self.assertIn(('active', '=', True), pos_schema_domain)

        pos_line_domain = self.lines._load_pos_data_domain({}, None)
        self.assertIn(('active', '=', True), pos_line_domain)

        pos_pres_domain = self.presentations._load_pos_data_domain({}, None)
        self.assertIn(('line_id.active', '=', True), pos_pres_domain)
