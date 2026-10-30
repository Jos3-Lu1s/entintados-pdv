# -*- coding: utf-8 -*-

from datetime import timedelta

from lxml import etree

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestComboAgreements(TransactionCase):
    """El acuerdo comercial del producto combo define el precio del paquete: el precio fijo se
    reparte entre sus productos y el descuento lo heredan."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
            | cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
            | cls.env.ref('entintados_pdv.group_sale_edit_discount')
        )
        company = cls.env.company
        country = company.account_fiscal_country_id or cls.env.ref('base.mx')
        cls.iva = cls.env['account.tax'].create({
            'name': 'IVA Combo 16%',
            'type_tax_use': 'sale',
            'amount_type': 'percent',
            'amount': 16.0,
            'company_id': company.id,
            'country_id': country.id,
            'tax_group_id': cls.env['account.tax.group'].create({
                'name': 'IVA Combo',
                'company_id': company.id,
                'country_id': country.id,
            }).id,
        })
        products = cls.env['product.product']

        def product(name, price, taxes=cls.iva):
            return products.create({
                'name': name,
                'list_price': price,
                'invoice_policy': 'order',
                'taxes_id': [Command.set(taxes.ids)],
            })

        cls.vinilica = product('Vinílica Acuerdo', 500.0)
        cls.esmalte = product('Esmalte Acuerdo', 650.0)
        cls.rodillo_chico = product('Rodillo Chico Acuerdo', 150.0)
        cls.rodillo_grande = product('Rodillo Grande Acuerdo', 200.0)
        cls.brocha_2 = product('Brocha 2 Acuerdo', 50.0)
        cls.brocha_4 = product('Brocha 4 Acuerdo', 80.0)
        cls.combos = cls.env['product.combo'].create([{
            'name': 'Pintura',
            'combo_item_ids': [
                Command.create({'product_id': cls.vinilica.id}),
                Command.create({'product_id': cls.esmalte.id}),
            ],
        }, {
            'name': 'Rodillo',
            'combo_item_ids': [
                Command.create({'product_id': cls.rodillo_chico.id}),
                Command.create({'product_id': cls.rodillo_grande.id}),
            ],
        }, {
            'name': 'Brocha',
            'combo_item_ids': [
                Command.create({'product_id': cls.brocha_2.id}),
                Command.create({'product_id': cls.brocha_4.id}),
            ],
        }])
        cls.package = products.create({
            'name': 'Paquete Pintor Acuerdo',
            'type': 'combo',
            'list_price': 1000.0,
            'invoice_policy': 'order',
            'taxes_id': [Command.clear()],
            'combo_ids': [Command.set(cls.combos.ids)],
        })
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'Tarifa Combo Acuerdo'})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Combo Acuerdo',
            'is_customer': True,
            'phone': '1234567890',
        })
        cls.partner.property_product_pricelist = cls.pricelist
        cls.rules = cls.env['res.partner.discount.rule']

    def _fixed_price_rule(self, product, price):
        return self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': product.id,
            'fixed_price': price,
        })

    def _item(self, combo, product):
        return combo.combo_item_ids.filtered(lambda item: item.product_id == product)

    def _quote(self, choices=None, qty=3.0):
        """Cotización con el paquete y sus productos, igual que la que arma el configurador."""
        choices = choices or [self.vinilica, self.rodillo_grande, self.brocha_2]
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
        })
        package_line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.package.id,
            'product_uom_qty': qty,
        })
        items = self.env['sale.order.line'].create([{
            'order_id': order.id,
            'product_id': product.id,
            'product_uom_qty': qty,
            'combo_item_id': self._item(combo, product).id,
            'linked_line_id': package_line.id,
        } for combo, product in zip(self.combos, choices)])
        return order, package_line, items

    def test_fixed_price_is_distributed(self):
        self._fixed_price_rule(self.package, 200.0)
        order, package_line, items = self._quote()

        self.assertEqual(package_line.price_unit, 0.0)
        self.assertEqual(package_line.discount, 0.0)
        self.assertEqual(package_line.pricing_rule_type, 'fixed_price')
        self.assertEqual(package_line.pricing_rule_origin, 'Precio Fijo')
        self.assertEqual([round(price, 2) for price in items.mapped('price_unit')], [142.86, 42.86, 14.28])
        self.assertAlmostEqual(order.amount_untaxed, 600.0, places=2)
        self.assertAlmostEqual(order.amount_tax, 96.0, places=2)
        snapshot = items[0].combo_price_snapshot
        self.assertEqual(snapshot['pricelist_price'], 1000.0)
        self.assertEqual(snapshot['agreement']['price'], 200.0)
        self.assertEqual(snapshot['package_price'], 200.0)

    def test_fixed_price_invoice_matches_order(self):
        self._fixed_price_rule(self.package, 200.0)
        order, _package_line, _items = self._quote()
        order.action_confirm()

        invoice = order._create_invoices()

        self.assertAlmostEqual(order.amount_total, 696.0, places=2)
        self.assertAlmostEqual(invoice.amount_total, order.amount_total, places=2)

    def test_item_agreements_ignored_inside_combo(self):
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.vinilica.id,
            'discount': 15.0,
        })
        self._fixed_price_rule(self.brocha_2, 30.0)
        _order, _package_line, items = self._quote()
        pintura, _rodillo, brocha = items

        self.assertEqual(pintura.discount, 0.0)
        self.assertEqual(pintura.pricing_rule_type, 'none')
        self.assertEqual(round(brocha.price_unit, 2), 71.42)
        self.assertNotEqual(brocha.pricing_rule_type, 'fixed_price')

        loose = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'order_line': [
                Command.create({'product_id': self.vinilica.id}),
                Command.create({'product_id': self.brocha_2.id}),
            ],
        }).order_line
        self.assertEqual((loose[0].discount, loose[0].pricing_rule_type), (15.0, 'product'))
        self.assertEqual((round(loose[1].price_unit, 2), loose[1].pricing_rule_type), (30.0, 'fixed_price'))

    def _assert_items_inherit(self, package_line, items, discount, rule_type, origin):
        self.assertEqual(
            (package_line.discount, package_line.pricing_rule_type, package_line.pricing_rule_origin),
            (discount, rule_type, origin),
        )
        for line in items:
            self.assertEqual(
                (line.discount, line.technical_discount, line.pricing_rule_type,
                 line.pricing_rule_origin, line.pricing_rule_id),
                (discount, discount, rule_type, f'Del paquete: {origin}', package_line.pricing_rule_id),
                line.product_id.name,
            )

    def test_discount_agreement_is_inherited(self):
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': self.package.id,
            'discount': 10.0,
        })
        order, package_line, items = self._quote()

        self._assert_items_inherit(package_line, items, 10.0, 'product', 'Desc. Producto (10.0%)')
        self.assertEqual([round(price, 2) for price in items.mapped('price_unit')], [714.29, 214.29, 71.42])
        self.assertAlmostEqual(order.amount_untaxed, 2700.0, places=2)

    def test_global_discount_is_inherited(self):
        self.partner.discount = 0.05
        _order, package_line, items = self._quote()

        self._assert_items_inherit(package_line, items, 5.0, 'global', 'Desc. Global Cliente (5.0%)')

    def test_no_agreement_items_have_no_badge(self):
        _order, _package_line, items = self._quote()

        for line in items:
            self.assertEqual(
                (line.discount, line.pricing_rule_type, line.pricing_rule_origin, line.pricing_rule_id.id),
                (0.0, 'none', '', False),
            )

    def test_manual_discount_on_package_propagates(self):
        order, package_line, items = self._quote()

        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                self.assertEqual(line_form.product_id, self.package)
                line_form.discount = 7.0
            for index in range(1, 4):
                with order_form.order_line.edit(index) as item_form:
                    self.assertEqual(item_form.discount, 7.0)
                    self.assertEqual(item_form.pricing_rule_origin, 'Del paquete: Desc. Manual (7.0%)')

        self._assert_items_inherit(package_line, items, 7.0, 'manual', 'Desc. Manual (7.0%)')

        order.action_update_prices()
        self._assert_items_inherit(package_line, items, 7.0, 'manual', 'Desc. Manual (7.0%)')

    def test_fixed_price_package_rejects_manual_discount(self):
        self._fixed_price_rule(self.package, 200.0)
        _order, package_line, items = self._quote()

        with self.assertRaises(ValidationError):
            package_line.write({'discount': 5.0})
        self.assertEqual(items.mapped('discount'), [0.0, 0.0, 0.0])

    def test_saved_fixed_price_is_frozen(self):
        rule = self._fixed_price_rule(self.package, 200.0)
        order, package_line, items = self._quote()
        rule.fixed_price = 250.0

        order.order_line.write({'product_uom_qty': 5.0})
        self.assertEqual(package_line.combo_package_price, 200.0)
        self.assertEqual([round(price, 2) for price in items.mapped('price_unit')], [142.86, 42.86, 14.28])
        self.assertEqual(package_line.price_unit, 0.0)

        order.action_update_prices()
        self.assertEqual(package_line.combo_package_price, 250.0)
        self.assertEqual(package_line.price_unit, 0.0)
        self.assertEqual([round(price, 2) for price in items.mapped('price_unit')], [178.57, 53.57, 17.86])
        self.assertAlmostEqual(sum(items.mapped('price_unit')), 250.0, places=2)
        self.assertEqual(items[0].combo_price_snapshot['agreement']['price'], 250.0)

    def test_combo_items_lock_price_and_discount(self):
        _order, package_line, items = self._quote()

        self.assertTrue(package_line.manual_discount_allowed)
        self.assertEqual(items.mapped('manual_discount_allowed'), [False, False, False])

        arch = self.env['sale.order'].get_views([(False, 'form')])['views']['form']['arch']
        order_line = etree.fromstring(arch).xpath("//field[@name='order_line']")[0]
        for subview in ('list', 'form'):
            price_unit = order_line.xpath(f"./{subview}//field[@name='price_unit']")[0]
            self.assertIn('combo_item_id', price_unit.get('readonly') or '', subview)
            discount = order_line.xpath(f"./{subview}//field[@name='discount']")[0]
            self.assertIn('not manual_discount_allowed', discount.get('readonly') or '', subview)

    def test_snapshot_chain(self):
        self._fixed_price_rule(self.package, 200.0)
        _order, _package_line, items = self._quote()
        snapshot = items[0].combo_price_snapshot
        self.assertEqual(
            (snapshot['pricelist_price'], snapshot['agreement'], snapshot['package_price']),
            (1000.0, {'type': 'fixed_price', 'label': 'Precio Fijo', 'price': 200.0}, 200.0),
        )

        self.rules.search([('partner_id', '=', self.partner.id)]).unlink()
        _order, _package_line, items = self._quote()
        snapshot = items[0].combo_price_snapshot
        self.assertEqual(
            (snapshot['pricelist_price'], snapshot['agreement'], snapshot['package_price']),
            (1000.0, False, 1000.0),
        )

    def test_save_new_combo_lines_without_prices(self):
        """Como el `web_save` de una orden guardada: el paquete y sus productos llegan en un solo
        `create`, ligados por `linked_virtual_id` y sin precio ni descuento (de solo lectura)."""
        self._fixed_price_rule(self.package, 200.0)
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
        })
        choices = [self.vinilica, self.rodillo_grande, self.brocha_2]

        order.write({'order_line': [
            Command.create({
                'product_id': self.package.id,
                'product_uom_qty': 3.0,
                'virtual_id': 'combo-1',
                'sequence': 10,
            }),
            *[Command.create({
                'product_id': product.id,
                'product_uom_qty': 3.0,
                'combo_item_id': self._item(combo, product).id,
                'linked_virtual_id': 'combo-1',
                'sequence': 11 + index,
            }) for index, (combo, product) in enumerate(zip(self.combos, choices))],
        ]})

        package_line = order.order_line.filtered(lambda line: line.product_id == self.package)
        items = order.order_line - package_line
        self.assertEqual(items.linked_line_id, package_line)
        self.assertEqual(package_line.price_unit, 0.0)
        self.assertEqual([round(price, 2) for price in items.mapped('price_unit')], [142.86, 42.86, 14.28])
        self.assertEqual(items.mapped('pricing_rule_origin'), ['Del paquete: Precio Fijo'] * 3)
        self.assertAlmostEqual(order.amount_total, 696.0, places=2)

    def test_create_order_with_combo_lines_without_prices(self):
        """Orden nueva guardada de una vez: mismo lote, productos antes que su paquete en `vals`."""
        choices = [self.vinilica, self.rodillo_grande, self.brocha_2]
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'order_line': [
                *[Command.create({
                    'product_id': product.id,
                    'product_uom_qty': 3.0,
                    'combo_item_id': self._item(combo, product).id,
                    'linked_virtual_id': 'combo-1',
                    'sequence': 11 + index,
                }) for index, (combo, product) in enumerate(zip(self.combos, choices))],
                Command.create({
                    'product_id': self.package.id,
                    'product_uom_qty': 3.0,
                    'virtual_id': 'combo-1',
                    'sequence': 10,
                }),
            ],
        })

        package_line = order.order_line.filtered(lambda line: line.product_id == self.package)
        items = order.order_line - package_line
        self.assertEqual(items.linked_line_id, package_line)
        self.assertEqual([round(price, 2) for price in items.sorted('sequence').mapped('price_unit')],
                         [714.29, 214.29, 71.42])
        self.assertAlmostEqual(order.amount_untaxed, 3000.0, places=2)

    def test_copy_relinks_combo_lines_without_virtual_ids(self):
        """Al reconfigurar el combo de una orden guardada, el core liga sus productos solo por
        `linked_line_id`, que no se copia: duplicar la orden debe volver a ligarlos."""
        self._fixed_price_rule(self.package, 200.0)
        order, package_line, items = self._quote()
        self.assertFalse(package_line.virtual_id or any(items.mapped('linked_virtual_id')))

        copy = order.copy()

        new_package = copy.order_line.filtered(lambda line: line.product_id == self.package)
        new_items = copy.order_line - new_package
        self.assertEqual(new_items.linked_line_id, new_package)
        self.assertEqual(new_items.combo_item_id, items.combo_item_id)
        self.assertEqual([round(price, 2) for price in new_items.sorted('id').mapped('price_unit')],
                         [142.86, 42.86, 14.28])
        self.assertAlmostEqual(copy.amount_untaxed, 600.0, places=2)

    def _setup_other_currency(self, rate=17.0):
        """Moneda O distinta de la de la compañía, con `1 C = rate O`. Devuelve la tarifa en O."""
        company = self.env.company
        company_currency = company.currency_id
        usd, mxn = self.env.ref('base.USD'), self.env.ref('base.MXN')
        other = mxn if company_currency == usd else usd
        other.active = True
        self.env['res.currency.rate'].search([
            ('currency_id', 'in', (company_currency | other).ids),
            ('company_id', 'in', (company.id, False)),
        ]).unlink()
        self.env['res.currency.rate'].create({
            'name': fields.Date.today() - timedelta(days=30),
            'currency_id': other.id,
            'company_id': company.id,
            'rate': rate,
        })
        return self.env['product.pricelist'].create({'name': 'T-O Combo', 'currency_id': other.id})

    def _switch_pricelist_in_form(self, order, pricelist):
        """Cambia la tarifa en el formulario y devuelve los precios de los productos del combo
        antes de guardar."""
        with Form(order) as order_form:
            order_form.pricelist_id = pricelist
            screen = []
            for index in range(1, 4):
                with order_form.order_line.edit(index) as item_form:
                    screen.append(round(item_form.price_unit, 2))
        return screen

    def test_fixed_price_combo_currency_change_form(self):
        self._fixed_price_rule(self.package, 200.0)
        pricelist_o = self._setup_other_currency()
        order, package_line, items = self._quote()

        screen = self._switch_pricelist_in_form(order, pricelist_o)

        self.assertEqual(screen, [2428.57, 728.57, 242.86])
        self.assertAlmostEqual(package_line.combo_package_price, 3400.0, places=2)
        self.assertEqual([round(price, 2) for price in items.mapped('price_unit')], [2428.57, 728.57, 242.86])
        self.assertAlmostEqual(order.amount_untaxed, 10200.0, places=2)

    def test_fixed_price_combo_currency_change_write(self):
        self._fixed_price_rule(self.package, 200.0)
        pricelist_o = self._setup_other_currency()
        order, package_line, items = self._quote()

        order.write({'pricelist_id': pricelist_o.id})

        self.assertAlmostEqual(package_line.combo_package_price, 3400.0, places=2)
        self.assertEqual([round(price, 2) for price in items.mapped('price_unit')], [2428.57, 728.57, 242.86])
        self.assertAlmostEqual(order.amount_untaxed, 10200.0, places=2)

    def test_fixed_price_combo_qty_after_currency_change(self):
        self._fixed_price_rule(self.package, 200.0)
        pricelist_o = self._setup_other_currency()
        order, package_line, items = self._quote()
        order.write({'pricelist_id': pricelist_o.id})

        order.order_line.write({'product_uom_qty': 5.0})

        self.assertAlmostEqual(package_line.combo_package_price, 3400.0, places=2)
        self.assertEqual([round(price, 2) for price in items.mapped('price_unit')], [2428.57, 728.57, 242.86])
        self.assertAlmostEqual(order.amount_untaxed, 17000.0, places=2)

    def test_fixed_price_combo_currency_round_trip(self):
        """Con una tasa de muchos decimales, el precio repartido redondeado no volvería a 200."""
        self._fixed_price_rule(self.package, 200.0)
        pricelist_o = self._setup_other_currency(rate=0.0536409)
        order, package_line, items = self._quote()

        self._switch_pricelist_in_form(order, pricelist_o)
        self.assertAlmostEqual(package_line.combo_package_price, 200.0 * 0.0536409, places=6)
        self._switch_pricelist_in_form(order, self.pricelist)

        self.assertAlmostEqual(package_line.combo_package_price, 200.0, places=6)
        self.assertEqual([round(price, 2) for price in items.mapped('price_unit')], [142.86, 42.86, 14.28])
        self.assertAlmostEqual(order.amount_untaxed, 600.0, places=2)

    def test_combo_without_agreement_currency_change_form(self):
        pricelist_o = self._setup_other_currency()
        order, _package_line, items = self._quote()

        screen = self._switch_pricelist_in_form(order, pricelist_o)

        saved = [round(price, 2) for price in items.mapped('price_unit')]
        self.assertEqual(screen, saved)
        self.assertAlmostEqual(sum(saved), 17000.0, places=2)
        self.assertAlmostEqual(order.amount_untaxed, 51000.0, places=2)

    def test_new_combo_after_loose_line_uses_own_package(self):
        """Pedido sin guardar, como en el formulario: ninguna línea tiene `_origin`, y los
        productos del combo deben leer su propio paquete y no la primera línea del pedido."""
        self._fixed_price_rule(self.esmalte, 300.0)
        choices = [self.vinilica, self.rodillo_grande, self.brocha_2]
        order = self.env['sale.order'].new({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'order_line': [
                Command.create({'product_id': self.esmalte.id, 'sequence': 1}),
                Command.create({'product_id': self.package.id, 'virtual_id': 'combo-1', 'sequence': 10}),
                *[Command.create({
                    'product_id': product.id,
                    'combo_item_id': self._item(combo, product).id,
                    'linked_virtual_id': 'combo-1',
                    'sequence': 11 + index,
                }) for index, (combo, product) in enumerate(zip(self.combos, choices))],
            ],
        })
        loose, package_line, *items = order.order_line.sorted('sequence')
        self.assertEqual(loose.pricing_rule_type, 'fixed_price')

        for item in items:
            self.assertEqual(item._get_combo_package_line(), package_line, item.product_id.name)
            self.assertEqual((item.pricing_rule_type, item.pricing_rule_origin), ('none', ''), item.product_id.name)
        self.assertEqual([round(item.price_unit, 2) for item in items], [714.29, 214.29, 71.42])

    def test_combo_package_uom_is_readonly(self):
        self.env.user.group_ids |= self.env.ref('uom.group_uom')
        order, package_line, _items = self._quote()
        loose = self.env['sale.order.line'].create({'order_id': order.id, 'product_id': self.esmalte.id})

        self.assertTrue(package_line.product_uom_readonly)
        self.assertFalse(loose.product_uom_readonly)
        with Form(order) as order_form, order_form.order_line.edit(0) as package_form:
            with self.assertRaises(AssertionError):
                package_form.product_uom_id = self.env.ref('uom.product_uom_dozen')

    def test_combo_package_rejects_other_uom_write(self):
        order, package_line, _items = self._quote()

        with self.assertRaises(ValidationError):
            order.write({'order_line': [
                Command.update(package_line.id, {'product_uom_id': self.env.ref('uom.product_uom_dozen').id}),
            ]})

    def test_combo_package_rejects_other_uom_with_pricelist_change(self):
        """Tarifa a otra moneda y UdM del paquete en el mismo guardado."""
        self._fixed_price_rule(self.package, 200.0)
        pricelist_o = self._setup_other_currency()
        order, package_line, _items = self._quote()

        with self.assertRaises(ValidationError):
            order.write({
                'pricelist_id': pricelist_o.id,
                'order_line': [
                    Command.update(package_line.id, {'product_uom_id': self.env.ref('uom.product_uom_dozen').id}),
                ],
            })

        self.env.invalidate_all()
        self.assertEqual(order.pricelist_id, self.pricelist)
        self.assertAlmostEqual(order.amount_untaxed, 600.0, places=2)

    def test_combo_package_rejects_other_uom_create(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
        })

        with self.assertRaises(ValidationError):
            self.env['sale.order.line'].create({
                'order_id': order.id,
                'product_id': self.package.id,
                'product_uom_id': self.env.ref('uom.product_uom_dozen').id,
            })

    def test_combo_product_rejects_extra_uoms(self):
        dozen = self.env.ref('uom.product_uom_dozen')
        # Viene archivada: escrita desde `product.product`, el núcleo la descartaría sin guardarla.
        dozen.active = True

        with self.assertRaisesRegex(ValidationError, 'unidades adicionales'):
            self.package.write({'uom_ids': [Command.set(dozen.ids)]})

        loose = self.env['product.product'].create({
            'name': 'Producto con Docena',
            'uom_ids': [Command.set(dozen.ids)],
        })
        with self.assertRaisesRegex(ValidationError, 'unidades adicionales'):
            loose.product_tmpl_id.write({'type': 'combo', 'combo_ids': [Command.set(self.combos.ids)]})

    def test_combo_package_uom_hidden_in_mobile_views(self):
        """En escritorio la fila del combo no muestra la UdM; la tarjeta y el formulario móviles tampoco."""
        self.env.user.group_ids |= self.env.ref('uom.group_uom')
        arch = self.env['sale.order'].get_views([(False, 'form')])['views']['form']['arch']
        order_line = etree.fromstring(arch).xpath("//field[@name='order_line']")[0]
        for subview in ('kanban', 'form'):
            uom = order_line.xpath(f"./{subview}//field[@name='product_uom_id']")[0]
            self.assertEqual(uom.get('invisible'), "product_type == 'combo'", subview)
