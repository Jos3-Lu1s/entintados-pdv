# -*- coding: utf-8 -*-

from odoo import Command
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestComboPriceBreakdown(TransactionCase):
    """Foto del reparto nativo del precio de un combo entre sus productos en ventas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
            | cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
            | cls.env.ref('entintados_pdv.group_sale_edit_discount')
        )
        products = cls.env['product.product']

        def product(name, price):
            return products.create({'name': name, 'list_price': price, 'taxes_id': [(5, 0, 0)]})

        cls.vinilica = product('Vinílica Combo', 500.0)
        cls.esmalte = product('Esmalte Combo', 650.0)
        cls.rodillo_chico = product('Rodillo Chico Combo', 150.0)
        cls.rodillo_grande = product('Rodillo Grande Combo', 200.0)
        cls.brocha_2 = product('Brocha 2 Combo', 50.0)
        cls.brocha_4 = product('Brocha 4 Combo', 80.0)
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
            'name': 'Paquete Pintor',
            'type': 'combo',
            'list_price': 1000.0,
            'taxes_id': [(5, 0, 0)],
            'combo_ids': [Command.set(cls.combos.ids)],
        })
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'Tarifa Combo'})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Combo',
            'is_customer': True,
            'phone': '1234567890',
        })
        cls.partner.property_product_pricelist = cls.pricelist

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

    def test_snapshot_matches_native_price(self):
        _order, _package_line, items = self._quote()

        self.assertEqual(items.mapped('price_unit'), [714.29, 214.29, 71.42])
        for line in items:
            snapshot = line.combo_price_snapshot
            self.assertTrue(snapshot, line.product_id.name)
            self.assertAlmostEqual(snapshot['unit_price'], line.price_unit, places=2)
            self.assertEqual(snapshot['combo_id'], line.combo_item_id.combo_id.id)
            self.assertEqual(snapshot['package_price'], 1000.0)
            self.assertEqual(snapshot['total_reference'], 700.0)
            self.assertEqual(
                [(group['name'], group['reference'], group['reference_product'])
                 for group in snapshot['groups']],
                [
                    ('Pintura', 500.0, self.vinilica.display_name),
                    ('Rodillo', 150.0, self.rodillo_chico.display_name),
                    ('Brocha', 50.0, self.brocha_2.display_name),
                ],
            )

    def test_rounding_adjustment_on_last_combo(self):
        _order, _package_line, items = self._quote()
        pintura, rodillo, brocha = (line.combo_price_snapshot for line in items)

        self.assertEqual(pintura['rounding_adjustment'], 0.0)
        self.assertEqual(rodillo['rounding_adjustment'], 0.0)
        self.assertAlmostEqual(brocha['share_raw'], 71.4286, places=4)
        self.assertEqual(brocha['share_rounded'], 71.43)
        self.assertAlmostEqual(brocha['rounding_adjustment'], -0.01, places=2)
        self.assertAlmostEqual(sum(s['unit_price'] for s in (pintura, rodillo, brocha)), 1000.0, places=2)

    def test_extra_price_and_attributes(self):
        self._item(self.combos[0], self.esmalte).extra_price = 100.0
        mango = self.env['product.attribute'].create({
            'name': 'Mango Combo',
            'create_variant': 'no_variant',
            'value_ids': [Command.create({'name': 'Madera'}), Command.create({'name': 'Plástico'})],
        })
        brocha_mango = self.env['product.template'].create({
            'name': 'Brocha Mango Combo',
            'list_price': 60.0,
            'taxes_id': [(5, 0, 0)],
            'attribute_line_ids': [Command.create({
                'attribute_id': mango.id,
                'value_ids': [Command.set(mango.value_ids.ids)],
            })],
        })
        madera = brocha_mango.attribute_line_ids.product_template_value_ids.filtered(
            lambda ptav: ptav.name == 'Madera')
        madera.price_extra = 10.0
        brocha_mango = brocha_mango.product_variant_id
        self.combos[2].combo_item_ids = [Command.create({'product_id': brocha_mango.id})]

        _order, _package_line, items = self._quote(
            choices=[self.esmalte, self.rodillo_chico, brocha_mango])
        items[2].product_no_variant_attribute_value_ids = [Command.set(madera.ids)]
        items[2].with_context(force_price_recomputation=True)._compute_price_unit()
        pintura, _rodillo, brocha = (line.combo_price_snapshot for line in items)

        self.assertEqual(pintura['extra_price'], 100.0)
        self.assertEqual(pintura['share_rounded'], 714.29)
        self.assertAlmostEqual(pintura['unit_price'], 814.29, places=2)
        self.assertAlmostEqual(items[0].price_unit, 814.29, places=2)
        self.assertEqual(brocha['attributes_extra'], 10.0)
        self.assertAlmostEqual(brocha['unit_price'], 81.42, places=2)
        self.assertAlmostEqual(items[2].price_unit, 81.42, places=2)

    def test_zero_total_reference(self):
        products = self.env['product.product'].create([
            {'name': f'Gratis Combo {i}', 'list_price': 0.0, 'taxes_id': [(5, 0, 0)]}
            for i in range(2)
        ])
        combos = self.env['product.combo'].create([
            {'name': f'Grupo Gratis {i}', 'combo_item_ids': [Command.create({'product_id': p.id})]}
            for i, p in enumerate(products)
        ])
        self.package.combo_ids = [Command.set(combos.ids)]
        self.combos = combos

        _order, _package_line, items = self._quote(choices=products)
        first, last = (line.combo_price_snapshot for line in items)

        self.assertEqual(items.mapped('price_unit'), [0.0, 1000.0])
        self.assertEqual(first['total_reference'], 0.0)
        self.assertEqual((first['share_rounded'], first['rounding_adjustment']), (0.0, 0.0))
        self.assertEqual((last['share_rounded'], last['rounding_adjustment']), (0.0, 1000.0))
        self.assertEqual([first['unit_price'], last['unit_price']], items.mapped('price_unit'))

    def test_snapshot_refreshes_on_quantity_and_pricelist(self):
        order, _package_line, items = self._quote()
        promo = self.env['product.pricelist'].create({
            'name': 'Tarifa Combo Mayoreo',
            'item_ids': [Command.create({
                'applied_on': '1_product',
                'product_tmpl_id': self.package.product_tmpl_id.id,
                'compute_price': 'fixed',
                'fixed_price': 900.0,
                'min_quantity': 5.0,
            })],
        })

        order.pricelist_id = promo
        order.action_update_prices()
        snapshot = items[0].combo_price_snapshot
        self.assertEqual((snapshot['package_price'], snapshot['package_price_origin']),
                         (1000.0, 'Precio de venta'))

        order.order_line.write({'product_uom_qty': 5.0})
        snapshots = items.mapped('combo_price_snapshot')
        self.assertEqual(snapshots[0]['package_price'], 900.0)
        self.assertEqual(snapshots[0]['package_price_origin'], 'Tarifa: Tarifa Combo Mayoreo')
        self.assertEqual([s['share_rounded'] for s in snapshots], [642.86, 192.86, 64.29])
        self.assertAlmostEqual(snapshots[2]['rounding_adjustment'], -0.01, places=2)
        self.assertEqual(items.mapped('price_unit'), [642.86, 192.86, 64.28])
        for line, snapshot in zip(items, snapshots):
            self.assertAlmostEqual(snapshot['unit_price'], line.price_unit, places=2)

    def test_snapshot_keeps_old_numbers(self):
        _order, _package_line, items = self._quote()
        before = items.mapped('combo_price_snapshot')

        self.vinilica.lst_price = 400.0
        self.env.invalidate_all()

        self.assertEqual(items.mapped('combo_price_snapshot'), before)
        self.assertEqual(before[0]['groups'][0]['reference'], 500.0)
        self.assertEqual(items.mapped('price_unit'), [714.29, 214.29, 71.42])

    def test_combo_items_price_origin(self):
        _order, _package_line, items = self._quote()

        self.assertEqual(items.mapped('price_origin'), ['combo'] * 3)
        self.assertEqual(items.mapped('price_origin_label'), [
            'Parte del combo «Paquete Pintor»: Pintura',
            'Parte del combo «Paquete Pintor»: Rodillo',
            'Parte del combo «Paquete Pintor»: Brocha',
        ])

        items[0].write({'price_unit': 750.0})
        self.assertEqual(items[0].price_origin, 'manual')
        self.assertEqual(items[0].price_unit, 750.0)
