# -*- coding: utf-8 -*-

from lxml import etree

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestProductDefaultCode(TransactionCase):
    """Referencia interna obligatoria en los canales de captura del usuario."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['product.template']
        cls.Product = cls.env['product.product']
        cls.size = cls.env['product.attribute'].create({
            'name': 'Tamaño Ref',
            'value_ids': [
                Command.create({'name': 'Chico'}),
                Command.create({'name': 'Grande'}),
            ],
        })

    def _multi_variant_vals(self, name):
        return {
            'name': name,
            'attribute_line_ids': [Command.create({
                'attribute_id': self.size.id,
                'value_ids': [Command.set(self.size.value_ids.ids)],
            })],
        }

    # --- Formularios (web_save / web_save_multi) -------------------------

    def test_web_save_new_template_without_code_fails(self):
        with self.assertRaises(ValidationError):
            self.Template.web_save({'name': 'Sin Ref Form'}, {})
        self.assertFalse(self.Template.search([('name', '=', 'Sin Ref Form')]))

    def test_web_save_new_template_cleans_code(self):
        result = self.Template.web_save(
            {'name': 'Con Ref Form', 'default_code': '  REF-01  '}, {})
        template = self.Template.browse(result[0]['id'])
        self.assertEqual(template.default_code, 'REF-01')
        self.assertEqual(template.product_variant_id.default_code, 'REF-01')

    def test_web_save_collapses_internal_spaces(self):
        result = self.Template.web_save(
            {'name': 'Ref Espacios Form', 'default_code': '  AB \t 12\n '}, {})
        self.assertEqual(self.Template.browse(result[0]['id']).default_code, 'AB 12')

    def test_web_save_blank_code_fails(self):
        with self.assertRaises(ValidationError):
            self.Template.web_save({'name': 'Ref Blanca', 'default_code': '   '}, {})
        self.assertFalse(self.Template.search([('name', '=', 'Ref Blanca')]))

    def test_web_save_clearing_existing_code_fails(self):
        template = self.Template.create({'name': 'Ref Borrada', 'default_code': 'BOR-1'})
        with self.assertRaises(ValidationError):
            template.web_save({'default_code': False}, {})
        self.assertEqual(template.default_code, 'BOR-1')

    def test_web_save_existing_template_without_code_fails_on_any_change(self):
        template = self.Template.create({'name': 'Legacy Sin Ref'})
        with self.assertRaises(ValidationError):
            template.web_save({'list_price': 15.0}, {})

    def test_web_save_multi_variant_template_does_not_require_code(self):
        result = self.Template.web_save(self._multi_variant_vals('Multi Form'), {})
        template = self.Template.browse(result[0]['id'])
        self.assertEqual(template.product_variant_count, 2)
        # Las variantes que genera el core quedan exentas.
        self.assertFalse(any(template.product_variant_ids.mapped('default_code')))
        template.web_save({'list_price': 20.0}, {})
        self.assertEqual(template.list_price, 20.0)

    def test_web_save_variant_without_code_fails(self):
        template = self.Template.create(self._multi_variant_vals('Multi Variante'))
        variant = template.product_variant_ids[0]
        with self.assertRaises(ValidationError):
            variant.web_save({'barcode': 'VAR-BAR-1'}, {})
        variant.web_save({'default_code': ' VAR  1 '}, {})
        self.assertEqual(variant.default_code, 'VAR 1')

    def test_web_save_new_variant_without_code_fails(self):
        with self.assertRaises(ValidationError):
            self.Product.web_save({'name': 'Variante Nueva Sin Ref'}, {})
        self.assertFalse(self.Product.search([('name', '=', 'Variante Nueva Sin Ref')]))

    def test_web_save_multi_without_code_fails(self):
        templates = self.Template.create([
            {'name': 'Lista A', 'default_code': 'LST-A'},
            {'name': 'Lista B', 'default_code': 'LST-B'},
        ])
        with self.assertRaises(ValidationError):
            templates.web_save_multi([{'default_code': False}, {'default_code': 'LST-B2'}], {})
        self.assertEqual(templates.mapped('default_code'), ['LST-A', 'LST-B'])

    # --- Importación (load) ----------------------------------------------

    def test_load_with_missing_code_imports_nothing(self):
        result = self.Template.load(
            ['name', 'default_code'],
            [['Import Uno', 'IMP-1'], ['Import Dos', '  ']],
        )
        self.assertFalse(result['ids'])
        errors = [m for m in result['messages'] if m['type'] == 'error']
        self.assertTrue(errors)
        self.assertIn('Import Dos', errors[0]['message'])
        self.assertFalse(self.Template.search([('name', 'in', ['Import Uno', 'Import Dos'])]))

    def test_load_with_codes_imports_products(self):
        result = self.Template.load(
            ['name', 'default_code'],
            [['Import Tres', ' IMP-3 '], ['Import Cuatro', 'IMP-4']],
        )
        self.assertEqual(len(result['ids']), 2)
        templates = self.Template.browse(result['ids'])
        self.assertEqual(templates.mapped('default_code'), ['IMP-3', 'IMP-4'])

    def test_load_variants_with_missing_code_imports_nothing(self):
        result = self.Product.load(['name', 'default_code'], [['Import Variante', '']])
        self.assertFalse(result['ids'])
        self.assertFalse(self.Product.search([('name', '=', 'Import Variante')]))

    # --- Creación rápida (name_create) -----------------------------------

    def test_name_create_is_blocked(self):
        with self.assertRaises(UserError):
            self.Template.name_create('Rápido Plantilla')
        with self.assertRaises(UserError):
            self.Product.name_create('Rápido Variante')

    # --- Creación por código (exenta) ------------------------------------

    def test_orm_create_without_code_is_allowed(self):
        template = self.Template.create({'name': 'Sistema Sin Ref'})
        self.assertFalse(template.default_code)
        product = self.Product.create({'name': 'Sistema Variante Sin Ref'})
        self.assertFalse(product.default_code)

    def test_orm_create_cleans_code(self):
        self.assertEqual(
            self.Template.create({'name': 'Sistema X', 'default_code': ' X '}).default_code, 'X')
        self.assertEqual(
            self.Product.create({'name': 'Sistema AB', 'default_code': '  AB   12  '}).default_code,
            'AB 12')

    def test_orm_write_cleans_code(self):
        product = self.Product.create({'name': 'Sistema Escritura'})
        product.write({'default_code': '   '})
        self.assertFalse(product.default_code)
        product.write({'default_code': ' W  1 '})
        self.assertEqual(product.default_code, 'W 1')

    # --- Vistas ----------------------------------------------------------

    def _default_code_node(self, model, view_xmlid):
        view = self.env.ref(view_xmlid)
        arch = self.env[model].get_view(view.id, 'form')['arch']
        return etree.fromstring(arch).xpath("//field[@name='default_code']")[0]

    def test_template_form_requires_code_when_visible(self):
        node = self._default_code_node('product.template', 'product.product_template_only_form_view')
        self.assertEqual(node.get('required'), 'product_variant_count <= 1')

    def test_variant_forms_require_code(self):
        for xmlid in ('product.product_normal_form_view', 'product.product_variant_easy_edit_view'):
            node = self._default_code_node('product.product', xmlid)
            self.assertIn(node.get('required'), ('1', 'True'), xmlid)
