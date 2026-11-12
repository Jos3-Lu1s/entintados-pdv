import re

from lxml import html

from odoo import Command
from odoo.tests.common import TransactionCase, tagged


def _digits(text):
    return re.sub(r'\D', '', text or '')


@tagged('post_install', '-at_install')
class TestSaleQuotationReport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_partner = cls.env['res.partner'].create({
            'name': 'Desarrollos Reporte SA',
            'is_company': True,
            'is_customer': True,
            'ref': '16159',
            'phone': '5551234567',
        })
        cls.contact = cls.env['res.partner'].create({
            'name': 'Esteban Contacto Reporte',
            'parent_id': cls.company_partner.id,
            'phone': '5551234568',
        })
        cls.tax = cls.env['account.tax'].create({
            'name': 'IVA reporte 16%',
            'amount': 16,
            'type_tax_use': 'sale',
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Brocha reporte',
            'default_code': 'BRO-REP-01',
            'list_price': 100.0,
            'taxes_id': [Command.set(cls.tax.ids)],
        })

        base_type = cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        size = cls.env['tint.size'].search([('code', '=', 'G')], limit=1)
        point = cls.env.ref('entintados_pdv.uom_tint_point')
        cls.base = cls.env['product.product'].create({
            'name': 'Base reporte',
            'default_code': 'BASE-REP-01',
            'tint_role': 'base',
            'tint_base_type_id': base_type.id,
            'tint_size_id': size.id,
            'list_price': 500.0,
            'taxes_id': [Command.clear()],
            'is_storable': True,
        })
        cls.colorant = cls.env['product.product'].create({
            'name': 'Colorante reporte',
            'default_code': 'COL-REP-01',
            'tint_role': 'colorant',
            'uom_id': point.id,
            'list_price': 2.0,
            'taxes_id': [Command.clear()],
            'is_storable': True,
        })
        color = cls.env['tint.color'].create({
            'name': 'Aguamarina Reporte',
            'gallery_id': cls.env['tint.gallery'].search([], limit=1).id,
            'code_number': '7781',
        })
        cls.formula = cls.env['tint.color.formula'].create({
            'color_id': color.id,
            'base_type_id': base_type.id,
            'size_id': size.id,
            'line_ids': [Command.create({'colorant_id': cls.colorant.id, 'points': 20})],
        })
        location = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1).lot_stock_id
        cls.env['stock.quant']._update_available_quantity(cls.base, location, 100)
        cls.env['stock.quant']._update_available_quantity(cls.colorant, location, 1000)

    def _order(self, partner=None, **values):
        return self.env['sale.order'].create({'partner_id': (partner or self.contact).id, **values})

    def _render(self, order):
        content = self.env['ir.actions.report']._render_qweb_html('sale.action_report_saleorder', order.ids)[0]
        return html.fromstring(content)

    def _text(self, nodes):
        return ' '.join(filter(None, (' '.join(node.text_content().split()) for node in nodes)))

    def _pairs(self, doc):
        return [self._text(row.xpath('./td[1]')) for row in doc.xpath("//table[hasclass('o_sq_pairs')]//tr")]

    def test_title_follows_order_state(self):
        order = self._order(order_line=[Command.create({'product_id': self.product.id})])
        title = self._text(self._render(order).xpath("//div[hasclass('o_sq_title')]"))
        self.assertEqual(title, 'Cotización')

        order.action_confirm()
        title = self._text(self._render(order).xpath("//div[hasclass('o_sq_title')]"))
        self.assertEqual(title, 'Orden de venta')

    def test_core_body_is_not_rendered(self):
        order = self._order(order_line=[Command.create({'product_id': self.product.id})])
        doc = self._render(order)
        self.assertTrue(doc.xpath("//table[@name='sale_quotation_lines']"))
        self.assertFalse(doc.xpath("//table[hasclass('o_main_table')]"))

    def test_attention_only_for_contact_of_a_company(self):
        order = self._order(order_line=[Command.create({'product_id': self.product.id})])
        doc = self._render(order)
        self.assertEqual(self._text(doc.xpath("//span[hasclass('o_sq_partner_name')]")), self.company_partner.name)
        self.assertIn('Atención', self._pairs(doc))
        self.assertIn(self.contact.name, doc.text_content())

        order = self._order(self.company_partner, order_line=[Command.create({'product_id': self.product.id})])
        self.assertNotIn('Atención', self._pairs(self._render(order)))

    def test_validity_rows_follow_validity_date(self):
        order = self._order(order_line=[Command.create({'product_id': self.product.id})])
        order.validity_date = '2026-12-31'
        doc = self._render(order)
        self.assertIn('Válida hasta', self._pairs(doc))
        self.assertIn('válida hasta', self._text(doc.xpath("//td[hasclass('o_sq_terms')]")))

        order.validity_date = False
        doc = self._render(order)
        self.assertNotIn('Válida hasta', self._pairs(doc))
        self.assertNotIn('válida hasta', self._text(doc.xpath("//td[hasclass('o_sq_terms')]")))

    def test_discount_column_and_rows_only_with_discount(self):
        order = self._order(order_line=[Command.create({'product_id': self.product.id, 'product_uom_qty': 2})])
        doc = self._render(order)
        headers = self._text(doc.xpath("//table[@name='sale_quotation_lines']//th"))
        self.assertNotIn('Desc.', headers)
        self.assertFalse(doc.xpath("//tr[@name='sale_quotation_gross']"))
        self.assertFalse(doc.xpath("//tr[@name='sale_quotation_discount']"))

        order.order_line.discount = 20
        doc = self._render(order)
        headers = self._text(doc.xpath("//table[@name='sale_quotation_lines']//th"))
        self.assertIn('Desc.', headers)
        self.assertTrue(doc.xpath("//tr[@name='sale_quotation_gross']"))
        self.assertTrue(doc.xpath("//tr[@name='sale_quotation_discount']"))
        values = order._quotation_report_values()
        self.assertAlmostEqual(values['gross'], 200.0)
        self.assertAlmostEqual(values['discount'], 40.0)
        self.assertAlmostEqual(values['gross'] - values['discount'], order.amount_untaxed)
        self.assertEqual(_digits(self._text(doc.xpath("//tr[@name='sale_quotation_discount']/td[2]"))), '4000')

    def test_tax_rows_and_total(self):
        order = self._order(order_line=[Command.create({'product_id': self.product.id})])
        doc = self._render(order)
        tax_rows = doc.xpath("//tr[@name='sale_quotation_tax']")
        groups = [group for subtotal in order.tax_totals['subtotals'] for group in subtotal['tax_groups']]
        self.assertEqual(len(tax_rows), len(groups))
        total = self._text(doc.xpath("//td[hasclass('o_sq_grand_value')]"))
        self.assertEqual(_digits(total), _digits('%.2f' % order.amount_total))

    def test_tint_prints_a_single_product_row(self):
        order = self._order()
        line = order._apply_tint(self.formula, self.base, 3)
        self.assertEqual(order.order_line, line)
        doc = self._render(order)

        self.assertFalse(doc.xpath("//tr[@name='sale_quotation_section']"))
        rows = doc.xpath("//tr[@name='sale_quotation_product']")
        self.assertEqual(len(rows), 1)
        cells = rows[0].xpath('./td')
        self.assertEqual(self._text(cells[:1]), 'BASE-REP-01')
        self.assertIn(self.formula.color_id.name.upper(), self._text(cells[1:2]).upper())
        self.assertEqual(_digits(self._text(cells[2:3])), '300')
        self.assertEqual(_digits(self._text(cells[-1:])), _digits('%.2f' % line.price_subtotal))
        self.assertNotIn('COL-REP-01', doc.text_content())

    def _section_order(self, **section_values):
        return self._order(order_line=[
            Command.create({'display_type': 'line_section', 'name': 'Áreas comunes', **section_values}),
            Command.create({'product_id': self.product.id, 'product_uom_qty': 2}),
        ])

    def test_section_with_hidden_composition_prints_total_without_children(self):
        order = self._section_order(collapse_composition=True)
        section = order.order_line.filtered('display_type')
        doc = self._render(order)

        cells = doc.xpath("//tr[@name='sale_quotation_section']")[0].xpath('./td')
        self.assertEqual(self._text(cells[:1]), 'Áreas comunes')
        self.assertFalse(self._text(cells[1:-1]))
        self.assertEqual(_digits(self._text(cells[-1:])),
                         _digits('%.2f' % section._get_section_totals('price_subtotal')))
        self.assertFalse(doc.xpath("//tr[@name='sale_quotation_product']"))

    def test_section_with_hidden_prices_prints_total_and_children_without_prices(self):
        order = self._section_order(collapse_prices=True)
        section = order.order_line.filtered('display_type')
        doc = self._render(order)

        cells = doc.xpath("//tr[@name='sale_quotation_section']")[0].xpath('./td')
        self.assertFalse(self._text(cells[1:-1]))
        self.assertEqual(_digits(self._text(cells[-1:])),
                         _digits('%.2f' % section._get_section_totals('price_subtotal')))
        children = doc.xpath("//tr[@name='sale_quotation_product']")
        self.assertEqual(len(children), 1)
        child_cells = children[0].xpath('./td')
        self.assertTrue(self._text(child_cells[2:3]))
        self.assertFalse(self._text(child_cells[3:]))

    def test_plain_section_prints_only_title(self):
        order = self._order(order_line=[
            Command.create({'display_type': 'line_section', 'name': 'Áreas comunes'}),
            Command.create({'product_id': self.product.id}),
        ])
        doc = self._render(order)
        row = doc.xpath("//tr[@name='sale_quotation_section']")[0]
        self.assertEqual(len(row.xpath('./td')), 1)
        self.assertEqual(self._text(row.xpath('./td')), 'Áreas comunes')
        product_cells = doc.xpath("//tr[@name='sale_quotation_product']/td")
        self.assertTrue(self._text(product_cells[-1:]))
