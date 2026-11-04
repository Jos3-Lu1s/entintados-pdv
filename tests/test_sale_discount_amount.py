# -*- coding: utf-8 -*-

from lxml import etree

from odoo import Command, fields
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import file_open


@tagged('post_install', '-at_install')
class TestSaleDiscountAmount(TransactionCase):
    """El importe del descuento se muestra sin IVA y en la moneda del pedido, sin cambiar sus valores."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
            | cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
            | cls.env.ref('entintados_pdv.group_sale_edit_discount')
        )
        cls.products = cls.env['product.product']
        cls.lines = cls.env['sale.order.line']
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Descuento en Dinero',
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Descuento en Dinero',
            'is_customer': True,
            'phone': '1234567890',
        })
        cls.partner.property_product_pricelist = cls.pricelist
        cls.product = cls._create_product('Pintura Descuento', 100.0)

    @classmethod
    def _create_product(cls, name, price, taxes=None):
        return cls.products.create({
            'name': name,
            'list_price': price,
            'invoice_policy': 'order',
            'taxes_id': [Command.set(taxes.ids if taxes else [])],
        })

    def _create_order(self, pricelist=None):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': (pricelist or self.pricelist).id,
        })

    def _create_line(self, order, product=None, qty=3.0, discount=0.0):
        line = self.lines.create({
            'order_id': order.id,
            'product_id': (product or self.product).id,
            'product_uom_qty': qty,
        })
        if discount:
            line.discount = discount
        return line

    def test_line_percentage(self):
        line = self._create_line(self._create_order(), discount=10.0)

        self.assertEqual(line.discount_amount, 30.0)

    def test_line_full_discount(self):
        line = self._create_line(self._create_order(), qty=2.0, discount=100.0)

        self.assertEqual(line.price_subtotal, 0.0)
        self.assertEqual(line.discount_amount, 200.0)

    def test_line_price_included_tax(self):
        company = self.env.company
        country = company.account_fiscal_country_id or self.env.ref('base.mx')
        tax = self.env['account.tax'].create({
            'name': 'IVA Incluido 16%',
            'type_tax_use': 'sale',
            'amount_type': 'percent',
            'amount': 16.0,
            'price_include_override': 'tax_included',
            'company_id': company.id,
            'country_id': country.id,
            'tax_group_id': self.env['account.tax.group'].create({
                'name': 'IVA Incluido',
                'company_id': company.id,
                'country_id': country.id,
            }).id,
        })
        product = self._create_product('Pintura IVA Incluido', 100.0, taxes=tax)
        line = self._create_line(self._create_order(), product=product, qty=1.0, discount=10.0)

        self.assertEqual(line.price_unit, 100.0)
        self.assertEqual(line.discount_amount, 8.62)

    def test_fixed_price_is_zero(self):
        self.env['res.partner.discount.rule'].create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.product.id,
            'fixed_price': 80.0,
        })
        line = self._create_line(self._create_order())

        self.assertEqual(line.pricing_rule_type, 'fixed_price')
        self.assertEqual(line.price_unit, 80.0)
        self.assertEqual(line.discount_amount, 0.0)

    def test_section_and_downpayment_are_zero(self):
        order = self._create_order()
        self._create_line(order, discount=10.0)
        section = self.lines.create({
            'order_id': order.id,
            'display_type': 'line_section',
            'name': 'Sección',
        })
        order.action_confirm()
        wizard = self.env['sale.advance.payment.inv'].with_context(
            active_model='sale.order', active_ids=order.ids,
        ).create({'advance_payment_method': 'percentage', 'amount': 10.0})
        wizard._create_invoices(order)
        down_payment = order.order_line.filtered(
            lambda line: line.is_downpayment and not line.display_type
        )

        self.assertTrue(down_payment)
        self.assertEqual(section.discount_amount, 0.0)
        self.assertEqual(down_payment.discount_amount, 0.0)
        self.assertEqual(order.amount_discount, 30.0)

    def test_order_total_matches_native(self):
        order = self._create_order()
        lines = (
            self._create_line(order, self._create_product('Ajuste A', 33.33), 7.0, 12.5)
            | self._create_line(order, self._create_product('Ajuste B', 19.99), 3.0, 7.0)
            | self._create_line(order, self._create_product('Ajuste C', 150.0), 1.0, 100.0)
            | self._create_line(order, self._create_product('Ajuste D', 45.5), 2.0)
        )

        self.assertEqual(order.amount_discount, order.currency_id.round(sum(lines.mapped('discount_amount'))))
        self.assertGreater(order.amount_discount, 0.0)
        self.assertAlmostEqual(
            order.amount_discount,
            order.amount_undiscounted - order.amount_untaxed,
            delta=0.01 * len(lines),
        )

    def test_combo_not_double_counted(self):
        combos = self.env['product.combo'].create([{
            'name': name,
            'combo_item_ids': [Command.create({
                'product_id': self._create_product(f'Componente {name}', price).id,
            })],
        } for name, price in (('Pintura', 500.0), ('Rodillo', 200.0), ('Brocha', 50.0))])
        package = self.products.create({
            'name': 'Paquete Descuento',
            'type': 'combo',
            'list_price': 1000.0,
            'invoice_policy': 'order',
            'taxes_id': [Command.clear()],
            'combo_ids': [Command.set(combos.ids)],
        })
        self.env['res.partner.discount.rule'].create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': package.id,
            'discount': 10.0,
        })
        order = self._create_order()
        package_line = self.lines.create({
            'order_id': order.id,
            'product_id': package.id,
            'product_uom_qty': 3.0,
        })
        items = self.lines.create([{
            'order_id': order.id,
            'product_id': combo.combo_item_ids.product_id.id,
            'product_uom_qty': 3.0,
            'combo_item_id': combo.combo_item_ids.id,
            'linked_line_id': package_line.id,
        } for combo in combos])
        items_amount = order.currency_id.round(sum(items.mapped('discount_amount')))

        self.assertEqual(package_line.discount, 10.0)
        self.assertAlmostEqual(items_amount, 300.0, delta=0.03)
        self.assertEqual(package_line.discount_amount, items_amount)
        self.assertEqual(order.amount_discount, items_amount)

    def test_recomputes_on_change(self):
        order = self._create_order()
        self._create_line(order, discount=10.0)

        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                line_form.discount = 20.0
                self.assertEqual(line_form.discount_amount, 60.0)
            self.assertEqual(order_form.amount_discount, 60.0)
            with order_form.order_line.edit(0) as line_form:
                line_form.product_uom_qty = 5.0
                self.assertEqual(line_form.discount_amount, 100.0)
            self.assertEqual(order_form.amount_discount, 100.0)

    def test_order_currency(self):
        company_currency = self.env.company.currency_id
        other_currency = self.env.ref(
            'base.MXN' if company_currency == self.env.ref('base.USD') else 'base.USD'
        )
        other_currency.active = True
        self.env['res.currency.rate'].search([
            ('currency_id', '=', other_currency.id),
            ('company_id', 'in', [self.env.company.id, False]),
        ]).unlink()
        self.env['res.currency.rate'].create({
            'name': fields.Date.today(),
            'currency_id': other_currency.id,
            'rate': 2.0,
            'company_id': self.env.company.id,
        })
        pricelist = self.env['product.pricelist'].create({
            'name': 'Tarifa Otra Moneda',
            'currency_id': other_currency.id,
        })
        order = self._create_order(pricelist)
        line = self._create_line(order, qty=1.0, discount=10.0)

        self.assertEqual(line.currency_id, other_currency)
        self.assertEqual(line.price_unit, 200.0)
        self.assertEqual(line.discount_amount, 20.0)
        self.assertEqual(order.amount_discount, 20.0)

    def test_values_unchanged(self):
        order = self._create_order()
        line = self._create_line(order, discount=10.0)
        self.assertEqual(line.discount_amount, 30.0)
        self.assertEqual(order.amount_discount, 30.0)

        self.assertEqual(line.price_unit, 100.0)
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.price_subtotal, 270.0)
        self.assertEqual(order.amount_untaxed, 270.0)
        self.assertEqual(order.amount_total, 270.0)

    def test_before_discount_adds_up(self):
        order = self._create_order()
        self._create_line(order, discount=10.0)
        self.assertEqual(order.amount_before_discount, 300.0)

        # Cada una descuenta 0.004: el total las redondea a 0 y el nativo las acumula.
        for index in range(3):
            rounding_product = self._create_product(f'Ajuste Redondeo {index}', 1.04)
            self._create_line(order, rounding_product, 1.0, 0.4)

        self.assertEqual(order.amount_discount, 30.0)
        self.assertEqual(order.amount_untaxed, 273.11)
        self.assertEqual(order.amount_before_discount, 303.11)
        self.assertEqual(order.currency_id.round(order.amount_undiscounted), 303.13)

    def test_before_discount_recomputes_on_change(self):
        order = self._create_order()
        self._create_line(order, discount=10.0)

        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                line_form.product_uom_qty = 5.0
            self.assertEqual(order_form.amount_before_discount, 500.0)
            with order_form.order_line.edit(0) as line_form:
                line_form.discount = 20.0
            self.assertEqual(order_form.amount_before_discount, 500.0)
            self.assertEqual(order_form.amount_discount, 100.0)

    def test_before_discount_view(self):
        arch = etree.fromstring(self.env.ref('entintados_pdv.view_order_form').arch)

        before = arch.xpath("//field[@name='amount_before_discount']")
        self.assertEqual(len(before), 1)
        self.assertEqual(
            before[0].xpath("ancestor-or-self::*[@groups][1]/@groups"),
            ['sale.group_discount_per_so_line'],
        )
        self.assertEqual(before[0].get('invisible'), '1')

    def test_totals_rows_in_native_table(self):
        with file_open('entintados_pdv/static/src/fields/tax_totals_discount/tax_totals_discount.xml', 'rb') as template:
            arch = etree.parse(template)

        extension = arch.xpath("//t[@t-inherit='account.TaxTotalsField']/xpath")[0]
        self.assertEqual(extension.get('expr'), "//t[@t-foreach='totals.subtotals']")
        self.assertEqual(extension.get('position'), 'before')
        blocks = extension.findall('t')
        self.assertEqual(
            [block.find('tr').get('name') for block in blocks],
            ['amount_before_discount', 'amount_discount'],
        )
        self.assertEqual(
            blocks[1].xpath('./tr/td[2]/span/@t-out'),
            ['formatMonetary(props.record.data.amount_discount)'],
        )
        for block in blocks:
            self.assertEqual(
                block.get('t-if'),
                "props.record.resModel === 'sale.order' and props.record.data.amount_discount",
            )
            self.assertEqual(len(block.findall('tr')), 1)
            self.assertEqual(block.xpath('./tr/td[1]/@class'), ['o_td_label'])
            self.assertEqual(block.xpath('./tr/td[1]/label/@class'), ['o_form_label o_tax_total_label'])
            self.assertEqual(block.xpath('./tr/td[2]/@class'), ['o_list_monetary'])

    def test_mobile_views_show_amount(self):
        arch = etree.fromstring(self.env['sale.order'].get_view(
            self.env.ref('sale.view_order_form').id,
        )['arch'])
        order_line = arch.xpath("//field[@name='order_line']")[0]

        card_amount = order_line.xpath("./kanban//t[@t-if='record.discount?.raw_value']//field[@name='discount_amount']")
        self.assertEqual(len(card_amount), 1)
        self.assertEqual(card_amount[0].getparent().text.strip(), 'Descuento:')
        form_amount = order_line.xpath("./form//field[@name='discount_amount']")
        self.assertEqual(len(form_amount), 1)
        self.assertTrue(form_amount[0].get('readonly'))

    def test_view_group(self):
        group = 'sale.group_discount_per_so_line'
        arch = etree.fromstring(self.env.ref('entintados_pdv.view_order_form').arch)

        column = arch.xpath("//xpath[@expr=\"//list/field[@name='discount']\"]/field[@name='discount_amount']")
        self.assertEqual(len(column), 1)
        self.assertEqual(column[0].get('groups'), group)
        self.assertEqual(column[0].get('optional'), 'show')

        total = arch.xpath("//field[@name='amount_discount']")
        self.assertEqual(len(total), 1)
        self.assertEqual(total[0].xpath("ancestor-or-self::*[@groups][1]/@groups"), [group])
        self.assertEqual(total[0].get('invisible'), '1')
