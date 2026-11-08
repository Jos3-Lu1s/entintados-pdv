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

    def test_tint_with_hidden_composition_prints_only_section(self):
        order = self._order()
        order._apply_tint(self.formula, self.base, 3)
        section = order.order_line.filtered('tint_snapshot')
        self.assertTrue(section.collapse_composition)
        doc = self._render(order)

        rows = doc.xpath("//tr[@name='sale_quotation_section']")
        self.assertEqual(len(rows), 1)
        cells = rows[0].xpath('./td')
        self.assertIn(section.name, self._text(cells[:1]))
        self.assertEqual(_digits(self._text(cells[1:2])), '300')
        self.assertEqual(_digits(self._text(cells[-1:])), _digits('%.2f' % section._get_section_totals('price_subtotal')))
        self.assertFalse(doc.xpath("//tr[@name='sale_quotation_product']"))

    def test_section_with_hidden_prices_prints_total_and_children_without_prices(self):
        order = self._order()
        order._apply_tint(self.formula, self.base, 1)
        section = order.order_line.filtered('tint_snapshot')
        section.write({'collapse_composition': False, 'collapse_prices': True})
        doc = self._render(order)

        section_row = doc.xpath("//tr[@name='sale_quotation_section']")[0]
        self.assertEqual(_digits(self._text(section_row.xpath('./td')[-1:])),
                         _digits('%.2f' % section._get_section_totals('price_subtotal')))
        children = doc.xpath("//tr[@name='sale_quotation_product']")
        self.assertEqual(len(children), 2)
        for row in children:
            cells = row.xpath('./td')
            self.assertTrue(self._text(cells[2:3]))
            self.assertFalse(self._text(cells[3:]))

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

    def _width(self, row):
        return sum(int(cell.get('colspan') or 1) for cell in row.xpath('./td|./th'))

    def _tint_order(self, quantity, **section_values):
        order = self._order()
        order._apply_tint(self.formula, self.base, quantity)
        section = order.order_line.filtered('tint_snapshot')
        if section_values:
            section.write(section_values)
        return order, section

    def test_tint_header_prints_containers_and_total_without_hiding(self):
        order, section = self._tint_order(2, collapse_composition=False, collapse_prices=False)
        doc = self._render(order)

        row = doc.xpath("//tr[@name='sale_quotation_section']")[0]
        cells = row.xpath('./td')
        self.assertIn(section.name, self._text(cells[:1]))
        self.assertEqual(_digits(self._text(cells[1:2])), '200')
        self.assertFalse(self._text(cells[2:-1]))
        total = section._get_section_totals('price_subtotal')
        self.assertEqual(_digits(self._text(cells[-1:])), _digits('%.2f' % total))

        children = doc.xpath("//tr[@name='sale_quotation_product']")
        self.assertEqual(len(children), 2)
        for child in children:
            self.assertTrue(self._text(child.xpath('./td')[3:4]))
            self.assertTrue(self._text(child.xpath('./td')[-1:]))
        components = order.order_line.filtered(lambda line: line.tint_section_id == section)
        self.assertAlmostEqual(total, sum(components.mapped('price_subtotal')))

    def test_tint_header_with_hidden_prices_prints_containers_and_total(self):
        order, section = self._tint_order(2, collapse_composition=False, collapse_prices=True)
        doc = self._render(order)

        cells = doc.xpath("//tr[@name='sale_quotation_section']")[0].xpath('./td')
        self.assertEqual(_digits(self._text(cells[1:2])), '200')
        self.assertEqual(_digits(self._text(cells[-1:])),
                         _digits('%.2f' % section._get_section_totals('price_subtotal')))
        for child in doc.xpath("//tr[@name='sale_quotation_product']"):
            self.assertFalse(self._text(child.xpath('./td')[3:]))

    def test_tint_header_keeps_table_width_with_discount_column(self):
        order, section = self._tint_order(1, collapse_composition=False, collapse_prices=False)
        order.write({'order_line': [Command.create({'product_id': self.product.id, 'discount': 20})]})
        doc = self._render(order)

        header_width = self._width(doc.xpath("//table[@name='sale_quotation_lines']/thead/tr")[0])
        self.assertEqual(header_width, 6)
        row = doc.xpath("//tr[@name='sale_quotation_section']")[0]
        self.assertEqual(self._width(row), header_width)
        cells = row.xpath('./td')
        self.assertEqual(_digits(self._text(cells[1:2])), '100')
        self.assertFalse(self._text(cells[2:-1]))

    def test_tint_header_without_containers_prints_empty_quantity(self):
        order, section = self._tint_order(1, collapse_composition=False, collapse_prices=False)
        self.env.cr.execute('UPDATE sale_order_line SET tint_container_qty = 0 WHERE id = %s', [section.id])
        section.invalidate_recordset(['tint_container_qty'])
        doc = self._render(order)

        cells = doc.xpath("//tr[@name='sale_quotation_section']")[0].xpath('./td')
        self.assertFalse(self._text(cells[1:2]))
        self.assertEqual(_digits(self._text(cells[-1:])),
                         _digits('%.2f' % section._get_section_totals('price_subtotal')))
