# -*- coding: utf-8 -*-

from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPricelistItemEsquema(TransactionCase):
    """ Una regla «Aplicar a = Esquema» es global: no guarda producto,
    variante ni categoría, aunque antes los tuviera.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.items = cls.env['product.pricelist.item']
        cls.scheme = cls.env['tint.schema'].create({'name': 'Esquema A SPEC32'})
        cls.line = cls.env['lines.product'].create({'name': 'Línea SPEC32', 'scheme': cls.scheme.id})
        cls.categ_products = cls.env['product.category'].create({'name': 'Categoría productos SPEC32'})
        cls.categ_other = cls.env['product.category'].create({'name': 'Categoría otra SPEC32'})
        cls.p1 = cls._template('SPEC32-P1', 300.0)
        cls.p2 = cls._template('SPEC32-P2', 200.0)
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'Tarifa SPEC32'})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente SPEC32',
            'is_customer': True,
            'phone': '5512345678',
            'property_product_pricelist': cls.pricelist.id,
        })

    @classmethod
    def _template(cls, code, price, **extra):
        return cls.env['product.template'].create({
            'name': f'Producto {code}',
            'default_code': code,
            'list_price': price,
            'categ_id': cls.categ_products.id,
            'lines_product_id': cls.line.id,
            **extra,
        })

    def _form(self, record=None):
        if record:
            return Form(record)
        # compute_price solo es editable con product.group_product_pricelist.
        return Form(self.items.with_context(
            default_pricelist_id=self.pricelist.id,
            default_compute_price='percentage',
        ))

    def _set_scheme_20(self, form):
        form.display_applied_on = '3_esquema'
        form.esquema_id = self.scheme
        form.percent_price = 20.0

    def _product_rule(self, template, **extra):
        return self.items.create({
            'pricelist_id': self.pricelist.id,
            'display_applied_on': '1_product',
            'applied_on': '1_product',
            'product_tmpl_id': template.id,
            'compute_price': 'percentage',
            'percent_price': 20.0,
            **extra,
        })

    def _scheme_rule(self, **extra):
        return self.items.create({
            'pricelist_id': self.pricelist.id,
            'display_applied_on': '3_esquema',
            'esquema_id': self.scheme.id,
            'compute_price': 'percentage',
            'percent_price': 20.0,
            **extra,
        })

    def _sale_prices(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
        })
        prices = []
        for template in (self.p1, self.p2):
            line = self.env['sale.order.line'].create({
                'order_id': order.id,
                'product_id': template.product_variant_id.id,
                'product_uom_qty': 1.0,
            })
            prices.append(line.price_unit * (1 - line.discount / 100.0))
        return prices

    def _assert_global(self, item):
        self.assertEqual(item.display_applied_on, '3_esquema')
        self.assertEqual(item.applied_on, '3_global')
        self.assertFalse(item.product_tmpl_id)
        self.assertFalse(item.product_id)
        self.assertFalse(item.categ_id)

    # --- Formulario -----------------------------------------------------

    def test_direct_scheme_rule_applies_to_all(self):
        form = self._form()
        self._set_scheme_20(form)
        form.save()

        self.assertEqual(self._sale_prices(), [240.0, 160.0])

    def test_product_to_scheme_clears_product(self):
        form = self._form()
        form.display_applied_on = '1_product'
        form.product_tmpl_id = self.p1
        self._set_scheme_20(form)
        item = form.save()

        self._assert_global(item)
        self.assertEqual(self._sale_prices(), [240.0, 160.0])

    def test_category_to_scheme_clears_category(self):
        form = self._form()
        form.display_applied_on = '2_product_category'
        form.categ_id = self.categ_other
        self._set_scheme_20(form)
        item = form.save()

        self._assert_global(item)
        self.assertEqual(self._sale_prices(), [240.0, 160.0])

    def test_existing_product_rule_edited_to_scheme(self):
        item = self._product_rule(self.p1)

        form = self._form(item)
        self._set_scheme_20(form)
        form.save()

        self._assert_global(item)
        self.assertEqual(self._sale_prices(), [240.0, 160.0])

    def test_variant_to_scheme_clears_variant(self):
        attribute = self.env['product.attribute'].create({
            'name': 'Acabado SPEC32',
            'value_ids': [(0, 0, {'name': 'Mate'}), (0, 0, {'name': 'Satinado'})],
        })
        template = self._template('SPEC32-VAR', 250.0, attribute_line_ids=[(0, 0, {
            'attribute_id': attribute.id,
            'value_ids': [(6, 0, attribute.value_ids.ids)],
        })])
        self.assertEqual(len(template.product_variant_ids), 2)
        item = self._product_rule(
            template,
            applied_on='0_product_variant',
            product_id=template.product_variant_ids[0].id,
        )
        self.assertTrue(item.product_id)

        form = self._form(item)
        self._set_scheme_20(form)
        form.save()

        self._assert_global(item)

    # --- create / write -------------------------------------------------

    def test_create_scheme_with_product_drops_it(self):
        with_product = self._scheme_rule(product_tmpl_id=self.p1.id)
        self._assert_global(with_product)

        with_variant = self._scheme_rule(product_id=self.p1.product_variant_id.id)
        self._assert_global(with_variant)

        with_category = self._scheme_rule(categ_id=self.categ_other.id)
        self._assert_global(with_category)

    def test_write_to_scheme_drops_product(self):
        item = self._product_rule(self.p1)

        item.write({'display_applied_on': '3_esquema', 'esquema_id': self.scheme.id})

        self._assert_global(item)
        self.assertEqual(self._sale_prices(), [240.0, 160.0])

    def test_write_product_on_scheme_rule_ignored(self):
        item = self._scheme_rule()

        item.write({'product_tmpl_id': self.p1.id})
        self._assert_global(item)

        item.write({'categ_id': self.categ_other.id})
        self._assert_global(item)

        item.write({'applied_on': '1_product', 'product_tmpl_id': self.p1.id})
        self._assert_global(item)

    def test_write_mixed_recordset(self):
        scheme_item = self._scheme_rule()
        product_item = self._product_rule(self.p1)

        (scheme_item | product_item).write({'product_tmpl_id': self.p2.id})

        self._assert_global(scheme_item)
        self.assertEqual(product_item.applied_on, '1_product')
        self.assertEqual(product_item.product_tmpl_id, self.p2)

    def test_scheme_to_product_still_clears_scheme(self):
        form = self._form()
        self._set_scheme_20(form)
        form.linea_id = self.line
        form.display_applied_on = '1_product'

        self.assertFalse(form.esquema_id)
        self.assertFalse(form.linea_id)

        form.product_tmpl_id = self.p1
        item = form.save()
        self.assertEqual(item.applied_on, '1_product')
        self.assertEqual(item.product_tmpl_id, self.p1)
