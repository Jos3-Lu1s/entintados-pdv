# -*- coding: utf-8 -*-

from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSchemeLineSelector(TransactionCase):
    """ El esquema se elige primero y filtra la línea; la línea sigue
    decidiendo el esquema que se guarda.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.templates = cls.env['product.template']
        cls.vinilica = cls.env['tint.schema'].create({'name': 'Vinílica'})
        cls.esmalte = cls.env['tint.schema'].create({'name': 'Esmalte'})
        cls.premium_vinilica = cls.env['lines.product'].create({
            'name': 'Premium', 'scheme': cls.vinilica.id,
        })
        cls.premium_esmalte = cls.env['lines.product'].create({
            'name': 'Premium', 'scheme': cls.esmalte.id,
        })
        cls.white = cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        cls.gallon = cls.env['tint.size'].search([('code', '=', 'G')], limit=1)

    def _base_form(self, record=None):
        if record:
            # Reabrir es otra petición: el esquema de filtro no se guarda y sale de la línea.
            record.invalidate_recordset()
            return Form(record)
        form = Form(self.templates)
        form.name = 'Base selector de esquema'
        form.default_code = 'SPEC25-BASE'
        form.is_storable = True
        form.tint_role = 'base'
        form.tint_base_type_id = self.white
        form.tint_size_id = self.gallon
        return form

    # --- Nombre de la línea ---------------------------------------------

    def test_line_short_display_name(self):
        line = self.premium_vinilica
        self.assertEqual(line.with_context(lines_product_short_name=True).display_name, 'Premium')
        self.assertEqual(line.display_name, 'Premium - Vinílica')

    # --- Producto -------------------------------------------------------

    def test_product_scheme_first(self):
        form = self._base_form()

        form.scheme_filter_id = self.vinilica
        self.assertEqual(form.scheme_filter_id, self.vinilica)
        self.assertFalse(form.lines_product_id)

        form.lines_product_id = self.premium_vinilica
        self.assertEqual(form.scheme_filter_id, self.vinilica)

        form.scheme_filter_id = self.esmalte
        self.assertEqual(form.scheme_filter_id, self.esmalte)
        self.assertFalse(form.lines_product_id)

        form.scheme_filter_id = self.vinilica
        form.lines_product_id = self.premium_vinilica
        form.lines_product_id = self.env['lines.product']
        self.assertEqual(form.scheme_filter_id, self.vinilica)

        form.lines_product_id = self.premium_vinilica
        product = form.save()
        self.assertEqual(product.scheme_id, self.vinilica)

        form = self._base_form(product)
        self.assertEqual(form.scheme_filter_id, self.vinilica)
        self.assertEqual(form.lines_product_id, self.premium_vinilica)

        form.tint_role = 'colorant'
        self.assertFalse(form.lines_product_id)
        self.assertFalse(form.scheme_filter_id)
        product = form.save()
        self.assertFalse(product.scheme_id)

    def test_product_line_first_fills_scheme(self):
        form = self._base_form()
        form.lines_product_id = self.premium_esmalte
        self.assertEqual(form.scheme_filter_id, self.esmalte)
        self.assertEqual(form.save().scheme_id, self.esmalte)

    def test_product_scheme_without_line_not_saved(self):
        form = self._base_form()
        form.scheme_filter_id = self.vinilica
        product = form.save()
        self.assertFalse(product.scheme_id)
        self.assertFalse(self._base_form(product).scheme_filter_id)

    # --- Fórmula de color -----------------------------------------------

    def test_formula_scheme_first(self):
        form = Form(self.env['tint.color.formula'])
        gallery = self.env['tint.gallery'].create({'name': 'Galería SPEC 25', 'code': 'S25-GAL'})
        form.color_id = self.env['tint.color'].create({
            'name': 'Color SPEC 25', 'code': 'S25-COL', 'gallery_id': gallery.id})
        form.base_type_id = self.white
        form.size_id = self.gallon

        form.line_scheme_id = self.premium_esmalte
        self.assertEqual(form.scheme_filter_id, self.esmalte)

        form.scheme_filter_id = self.vinilica
        self.assertFalse(form.line_scheme_id)
        self.assertEqual(form.scheme_filter_id, self.vinilica)

        form.line_scheme_id = self.premium_vinilica
        form.line_scheme_id = self.env['lines.product']
        self.assertEqual(form.scheme_filter_id, self.vinilica)
        formula = form.save()
        self.assertFalse(formula.scheme_id)
        formula.invalidate_recordset()
        self.assertFalse(Form(formula).scheme_filter_id)

        formula.line_scheme_id = self.premium_vinilica
        formula.invalidate_recordset()
        form = Form(formula)
        self.assertEqual(form.scheme_filter_id, self.vinilica)
        self.assertEqual(formula.scheme_id, self.vinilica)

    # --- Acuerdo del cliente --------------------------------------------

    def test_discount_rule_scheme_filter(self):
        partner = self.env['res.partner'].create({
            'name': 'Cliente SPEC 25',
            'is_customer': True,
            'phone': '1234567890',
        })
        with Form(partner) as partner_form:
            with partner_form.discount_rule_ids.new() as rule_form:
                rule_form.applied_on = '1_line'
                rule_form.line_id = self.premium_esmalte
                self.assertEqual(rule_form.scheme_id, self.esmalte)

                rule_form.scheme_id = self.vinilica
                self.assertFalse(rule_form.line_id)

                rule_form.line_id = self.premium_vinilica
                rule_form.discount = 10.0

        rule = partner.discount_rule_ids
        self.assertEqual(rule.line_id, self.premium_vinilica)
        self.assertEqual(rule.scheme_id, self.vinilica)
        self.assertEqual(rule.name, 'Premium - Vinílica: 10.00%')

    def test_discount_rule_line_decides_scheme(self):
        partner = self.env['res.partner'].create({
            'name': 'Cliente SPEC 25 por código',
            'is_customer': True,
            'phone': '1234567890',
        })
        rule = self.env['res.partner.discount.rule'].create({
            'partner_id': partner.id,
            'applied_on': '1_line',
            'line_id': self.premium_vinilica.id,
            'scheme_id': self.esmalte.id,
            'discount': 5.0,
        })
        self.assertEqual(rule.scheme_id, self.vinilica)

        rule.line_id = self.premium_esmalte
        self.assertEqual(rule.scheme_id, self.esmalte)

        otro = self.env['tint.schema'].create({'name': 'Otro esquema'})
        self.premium_esmalte.scheme = otro
        self.assertEqual(rule.scheme_id, otro)

        # Una regla por línea no cuenta como acuerdo del esquema.
        scheme_rule = self.env['res.partner.discount.rule'].create({
            'partner_id': partner.id,
            'applied_on': '2_scheme',
            'scheme_id': otro.id,
            'discount': 7.0,
        })
        self.assertEqual(scheme_rule.scheme_id, otro)

    # --- Regla de tarifa ------------------------------------------------

    def test_pricelist_item_scheme_clears_line(self):
        pricelist = self.env['product.pricelist'].create({'name': 'Tarifa SPEC 25'})
        form = Form(self.env['product.pricelist.item'].with_context(default_pricelist_id=pricelist.id))
        form.display_applied_on = '3_esquema'
        form.esquema_id = self.vinilica
        form.linea_id = self.premium_vinilica

        form.esquema_id = self.esmalte
        self.assertFalse(form.linea_id)

        form.linea_id = self.premium_esmalte
        form.esquema_id = self.esmalte
        self.assertEqual(form.linea_id, self.premium_esmalte)

    # --- Asistente de tarifas -------------------------------------------

    def test_wizard_line_mode_keeps_scheme(self):
        product = self.templates.create({
            'name': 'Base Premium Vinílica',
            'default_code': 'SPEC25-WIZ',
            'is_storable': True,
            'tint_role': 'base',
            'tint_base_type_id': self.white.id,
            'tint_size_id': self.gallon.id,
            'lines_product_id': self.premium_vinilica.id,
        })
        pricelist = self.env['product.pricelist'].create({'name': 'Tarifa asistente SPEC 25'})
        wizard_model = self.env['product.pricelist.wizard'].with_context(default_pricelist_id=pricelist.id)

        form = Form(wizard_model)
        form.esquema_id = self.vinilica
        form.apply_by = 'linea'
        self.assertEqual(form.esquema_id, self.vinilica)

        form.linea_id = self.premium_vinilica
        form.esquema_id = self.esmalte
        self.assertFalse(form.linea_id)

        form.esquema_id = self.vinilica
        form.linea_id = self.premium_vinilica
        with_scheme = form.save()

        form = Form(wizard_model)
        form.apply_by = 'linea'
        form.linea_id = self.premium_vinilica
        without_scheme = form.save()

        self.assertEqual(with_scheme._get_target_products(), product)
        self.assertEqual(without_scheme._get_target_products(), product)

        form = Form(wizard_model)
        form.apply_by = 'linea'
        form.linea_id = self.premium_vinilica
        form.apply_by = 'esquema'
        self.assertFalse(form.linea_id)
