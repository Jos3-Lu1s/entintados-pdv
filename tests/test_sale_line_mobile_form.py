# -*- coding: utf-8 -*-

from lxml import etree

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaleLineMobileForm(TransactionCase):
    """La línea de venta editada en pantalla pequeña (kanban + form inline) se comporta como la
    lista de escritorio.

    `odoo.tests.Form` siempre usa la `<list>`: aquí se arma el `fields_spec` del cliente móvil
    desde `get_views` y se ejecutan onchange y `web_save` como lo hace el navegador.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
            | cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
            | cls.env.ref('entintados_pdv.group_sale_edit_discount')
        )
        cls.orders = cls.env['sale.order']
        cls.lines = cls.env['sale.order.line']
        cls.rules = cls.env['res.partner.discount.rule']

        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa Móvil -5',
            'item_ids': [(0, 0, {
                'applied_on': '3_global', 'compute_price': 'percentage', 'percent_price': 5.0,
            })],
        })
        cls.product = cls.env['product.product'].create({'name': 'Pintura Móvil', 'list_price': 100.0})
        cls.partner = cls._create_partner('Cliente Móvil Acuerdo')
        cls.rules.create({
            'partner_id': cls.partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': cls.product.id,
            'discount': 10.0,
        })
        cls.partner_without_agreement = cls._create_partner('Cliente Móvil Sin Acuerdo')
        cls._load_mobile_spec()

    @classmethod
    def _create_partner(cls, name):
        partner = cls.env['res.partner'].create({
            'name': name,
            'is_customer': True,
            'phone': '1234567890',
        })
        partner.property_product_pricelist = cls.pricelist
        return partner

    @classmethod
    def _load_mobile_spec(cls):
        arch = etree.fromstring(cls.orders.get_views([(False, 'form')])['views']['form']['arch'])
        order_line = arch.xpath("//field[@name='order_line']")[0]
        cls.mobile_form = order_line.find('form')
        cls.mobile_kanban = order_line.find('kanban')
        cls.desktop_list = order_line.find('list')
        cls.mobile_nodes = {
            **cls._direct_fields(cls.mobile_kanban),
            **cls._direct_fields(cls.mobile_form),
        }
        cls.mobile_spec = {
            name: {'fields': {'display_name': {}}} if cls.lines._fields[name].relational else {}
            for name in cls.mobile_nodes
        }

    @staticmethod
    def _direct_fields(container):
        """Campos de la subvista, sin los de x2many anidados."""
        fields = {}
        for node in container.iter('field'):
            parent = node.getparent()
            while parent is not None and parent is not container and parent.tag != 'field':
                parent = parent.getparent()
            if parent is container:
                fields.setdefault(node.get('name'), node)
        return fields

    def _to_vals(self, values):
        """Valores que el cliente envía al guardar: los editables y los `force_save`."""
        vals = {}
        for name, value in values.items():
            field = self.lines._fields[name]
            node = self.mobile_nodes.get(name)
            force_save = node is not None and node.get('force_save') in ('1', 'True')
            readonly = node is not None and node.get('readonly') in ('1', 'True')
            if name == 'id' or field.type in ('one2many', 'many2many'):
                continue
            if (field.readonly or readonly) and not force_save:
                continue
            if field.type == 'many2one' and isinstance(value, dict):
                value = value.get('id')
            vals[name] = value
        return vals

    def _mobile_new_line(self, order, product, edits=()):
        """Diálogo de línea nueva: valores por defecto, producto y cada edición, y guardado del
        pedido. Devuelve la línea y los valores que devolvió el último onchange."""
        parent = {'id': order.id}
        values = self.lines.with_context(default_order_id=order.id).onchange(
            {'order_id': parent}, [], self.mobile_spec)['value']
        returned = {}
        for name, value in [('product_id', product.id), *edits]:
            values[name] = value
            sent = {
                key: val['id'] if isinstance(val, dict) and 'id' in val else val
                for key, val in values.items()
                if not isinstance(val, list)
            }
            sent['order_id'] = parent
            returned = self.lines.onchange(sent, [name], self.mobile_spec).get('value', {})
            values.update(returned)
        order.web_save({'order_line': [(0, 0, self._to_vals(values))]}, {'order_line': {'fields': {'id': {}}}})
        return order.order_line.sorted('id')[-1], returned

    def _mobile_edit_line(self, line, edits):
        """Abre una línea guardada, aplica cada edición con su onchange y guarda solo lo que cambió."""
        order = line.order_id
        initial = line.web_read(self.mobile_spec)[0]
        values = dict(initial)
        for name, value in edits:
            values[name] = value
            sent = {
                key: val['id'] if isinstance(val, dict) and 'id' in val else val
                for key, val in values.items()
                if not isinstance(val, list)
            }
            sent['order_id'] = {'id': order.id}
            values.update(self.lines.browse(line.id).onchange(sent, [name], self.mobile_spec).get('value', {}))
        changed = {key: val for key, val in values.items() if initial.get(key) != val}
        order.web_save({'order_line': [(1, line.id, self._to_vals(changed))]}, {})
        line.invalidate_recordset()
        return line

    def _new_order(self, partner=None):
        return self.orders.create({
            'partner_id': (partner or self.partner).id,
            'pricelist_id': self.pricelist.id,
        })

    def _badge(self, line):
        return line.pricing_rule_type, line.pricing_rule_origin

    def test_mobile_new_line_untouched_keeps_agreement(self):
        order = self._new_order()
        line, _returned = self._mobile_new_line(order, self.product)

        self.assertEqual(round(line.discount, 2), 10.0)
        self.assertEqual(self._badge(line), ('product', 'Desc. Producto (10.0%)'))

        order.write({'partner_id': self.partner_without_agreement.id})
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(line.pricing_rule_type, 'none')

    def test_mobile_new_line_typed_discount_is_manual(self):
        order = self._new_order()
        line, returned = self._mobile_new_line(order, self.product, [('discount', 7.0)])

        self.assertEqual(returned.get('discount', 7.0), 7.0)
        self.assertEqual(returned.get('pricing_rule_type'), 'manual')
        self.assertEqual(returned.get('pricing_rule_origin'), 'Desc. Manual (7.0%)')
        self.assertEqual(round(line.discount, 2), 7.0)
        self.assertEqual(self._badge(line), ('manual', 'Desc. Manual (7.0%)'))

    def test_mobile_new_line_typed_price_is_manual(self):
        order = self._new_order()
        line, returned = self._mobile_new_line(order, self.product, [('price_unit', 90.0)])

        self.assertEqual(returned.get('price_origin'), 'manual')
        self.assertEqual(line.price_unit, 90.0)
        self.assertEqual(line.price_origin, 'manual')

        order.write({'partner_id': self.partner_without_agreement.id})
        self.assertEqual(line.price_unit, 90.0)
        self.assertEqual(line.price_origin, 'manual')

    def test_mobile_form_readonly_matches_list(self):
        list_nodes = self._direct_fields(self.desktop_list)
        form_nodes = self._direct_fields(self.mobile_form)
        for name in ('price_unit', 'discount'):
            with self.subTest(field=name):
                self.assertTrue(form_nodes[name].get('readonly'))
                self.assertEqual(form_nodes[name].get('readonly'), list_nodes[name].get('readonly'))

    def test_mobile_form_shows_price_origin_and_badge(self):
        price_origin = self.mobile_form.xpath(".//div[@name='price_unit_origin']/field")
        self.assertEqual([node.get('name') for node in price_origin], ['price_unit', 'price_origin'])
        self.assertEqual(price_origin[1].get('widget'), 'price_origin_icon')
        self.assertEqual(price_origin[1].get('invisible'), 'is_downpayment')

        badge = self.mobile_form.xpath(".//field[@name='pricing_rule_origin']")
        self.assertEqual(len(badge), 1)
        self.assertEqual(badge[0].get('widget'), 'badge')
        self.assertIn('is_downpayment', self.mobile_nodes)

    def test_mobile_kanban_shows_price_origin_and_badge(self):
        kanban_nodes = self._direct_fields(self.mobile_kanban)
        self.assertEqual(kanban_nodes['price_origin'].get('widget'), 'price_origin_icon')
        self.assertEqual(kanban_nodes['price_origin'].get('invisible'), 'is_downpayment')
        self.assertEqual(kanban_nodes['pricing_rule_origin'].get('widget'), 'badge')
        for name in ('pricing_rule_type', 'price_origin_label', 'is_downpayment'):
            self.assertIn(name, kanban_nodes)

    def _rpc_create_line(self, partner, discount):
        return self.orders.create({
            'partner_id': partner.id,
            'pricelist_id': self.pricelist.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'discount': discount})],
        }).order_line

    def test_rpc_create_discount_against_agreement(self):
        line = self._rpc_create_line(self.partner, 10.0)
        self.assertEqual(round(line.discount, 2), 10.0)
        self.assertEqual(self._badge(line), ('product', 'Desc. Producto (10.0%)'))

        line = self._rpc_create_line(self.partner, 5.0)
        self.assertEqual(round(line.discount, 2), 5.0)
        self.assertEqual(self._badge(line), ('manual', 'Desc. Manual (5.0%)'))

        line = self._rpc_create_line(self.partner_without_agreement, 0.0)
        self.assertEqual(line.discount, 0.0)
        self.assertEqual(line.pricing_rule_type, 'none')

    def test_rpc_write_discount_against_agreement(self):
        line = self._rpc_create_line(self.partner, 7.0)
        self.assertEqual(self._badge(line), ('manual', 'Desc. Manual (7.0%)'))

        line.write({'discount': 10.0})
        self.assertEqual(round(line.discount, 2), 10.0)
        self.assertEqual(self._badge(line), ('product', 'Desc. Producto (10.0%)'))
        self.assertEqual(round(line.technical_discount, 2), 10.0)

        line.write({'discount': 8.0})
        self.assertEqual(round(line.discount, 2), 8.0)
        self.assertEqual(self._badge(line), ('manual', 'Desc. Manual (8.0%)'))

    def test_mobile_saved_line_typed_discount_is_manual(self):
        line, _returned = self._mobile_new_line(self._new_order(), self.product)
        self._mobile_edit_line(line, [('discount', 7.0)])
        self.assertEqual(round(line.discount, 2), 7.0)
        self.assertEqual(self._badge(line), ('manual', 'Desc. Manual (7.0%)'))

    def test_mobile_saved_line_typed_price_is_manual(self):
        line, _returned = self._mobile_new_line(self._new_order(), self.product)
        self._mobile_edit_line(line, [('price_unit', 88.0)])
        self.assertEqual(line.price_unit, 88.0)
        self.assertEqual(line.price_origin, 'manual')
        self.assertEqual(line.price_origin_label, 'Modificado de forma manual')

    def test_mobile_new_line_fixed_price_locks_discount(self):
        product = self.env['product.product'].create({'name': 'Pintura Móvil Fija', 'list_price': 100.0})
        self.rules.create({
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': product.id,
            'fixed_price': 70.0,
        })
        line, returned = self._mobile_new_line(self._new_order(), product)

        self.assertEqual(returned.get('pricing_rule_type'), 'fixed_price')
        self.assertIs(returned.get('manual_discount_allowed'), False)
        self.assertEqual(line.price_unit, 70.0)
        self.assertEqual(line.pricing_rule_type, 'fixed_price')


@tagged('post_install', '-at_install')
class TestComboMobileParity(TransactionCase):
    """En pantalla pequeña, las líneas de combo muestran y dejan editar lo mismo que la lista de
    escritorio, y el servidor rechaza lo que esa lista no deja cambiar."""

    _load_mobile_spec = TestSaleLineMobileForm.__dict__['_load_mobile_spec']
    _direct_fields = TestSaleLineMobileForm.__dict__['_direct_fields']
    _to_vals = TestSaleLineMobileForm._to_vals

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product.group_product_pricelist')
            | cls.env.ref('sale.group_discount_per_so_line')
            | cls.env.ref('uom.group_uom')
            | cls.env.ref('analytic.group_analytic_accounting')
            | cls.env.ref('stock.group_adv_location')
            | cls.env.ref('base.group_no_one')
        )
        cls.orders = cls.env['sale.order']
        cls.lines = cls.env['sale.order.line']
        products = cls.env['product.product']

        def product(name, price):
            return products.create({'name': name, 'list_price': price, 'taxes_id': [Command.clear()]})

        cls.vinilica = product('Vinílica Combo Móvil', 500.0)
        cls.esmalte = product('Esmalte Combo Móvil', 650.0)
        cls.rodillo = product('Rodillo Combo Móvil', 200.0)
        cls.brocha = product('Brocha Combo Móvil', 50.0)
        cls.combos = cls.env['product.combo'].create([{
            'name': 'Pintura Móvil',
            'combo_item_ids': [
                Command.create({'product_id': cls.vinilica.id}),
                Command.create({'product_id': cls.esmalte.id}),
            ],
        }, {
            'name': 'Rodillo Móvil',
            'combo_item_ids': [Command.create({'product_id': cls.rodillo.id})],
        }, {
            'name': 'Brocha Móvil',
            'combo_item_ids': [Command.create({'product_id': cls.brocha.id})],
        }])
        cls.package = products.create({
            'name': 'Paquete Combo Móvil',
            'type': 'combo',
            'list_price': 1000.0,
            'taxes_id': [Command.clear()],
            'combo_ids': [Command.set(cls.combos.ids)],
        })
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'Tarifa Combo Móvil'})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Combo Móvil',
            'is_customer': True,
            'phone': '1234567890',
        })
        cls.partner.property_product_pricelist = cls.pricelist
        cls.env['res.partner.discount.rule'].create({
            'partner_id': cls.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': cls.package.id,
            'fixed_price': 200.0,
        })
        cls._load_mobile_spec()

    def _quote(self, qty=3.0):
        order = self.orders.create({'partner_id': self.partner.id, 'pricelist_id': self.pricelist.id})
        package_line = self.lines.create({
            'order_id': order.id,
            'product_id': self.package.id,
            'product_uom_qty': qty,
        })
        items = self.lines.create([{
            'order_id': order.id,
            'product_id': product.id,
            'product_uom_qty': qty,
            'combo_item_id': combo.combo_item_ids.filtered(lambda item: item.product_id == product).id,
            'linked_line_id': package_line.id,
        } for combo, product in zip(self.combos, [self.vinilica, self.rodillo, self.brocha])])
        return order, package_line, items

    def _form_node(self, xpath):
        nodes = self.mobile_form.xpath(xpath)
        self.assertTrue(nodes, xpath)
        return nodes[0]

    def test_mobile_package_form_hides_desktop_hidden_fields(self):
        hidden = [
            ".//field[@name='price_unit']",
            ".//label[@for='price_unit']",
            ".//field[@name='tax_ids']",
            ".//label[@for='customer_lead']",
            ".//div[@name='lead']",
            ".//field[@name='analytic_distribution']",
            ".//field[@name='route_ids']",
            ".//widget[@name='qty_at_date_widget']",
            ".//label[@for='qty_delivered']",
            ".//div[@name='delivered_qty']",
            ".//label[@for='qty_invoiced']",
            ".//div[@name='invoiced_qty']",
            ".//div[@name='invoice_lines']",
        ]
        for xpath in hidden:
            with self.subTest(node=xpath):
                invisible = self._form_node(xpath).get('invisible') or ''
                # Las líneas de factura son de `base.group_no_one`: sin modo depuración llegan con
                # `invisible="1"` para todas las líneas.
                if invisible != '1':
                    self.assertIn("product_type == 'combo'", invisible)

        product = self._form_node(".//field[@name='product_id']")
        self.assertIn("product_type == 'combo'", product.get('readonly') or '')
        self.assertIn('no_open', product.get('options') or '')
        for xpath in (".//field[@name='product_uom_qty']", ".//field[@name='discount']",
                      ".//field[@name='name']", ".//field[@name='price_origin']",
                      ".//field[@name='pricing_rule_origin']"):
            with self.subTest(node=xpath):
                self.assertNotIn("product_type == 'combo'", self._form_node(xpath).get('invisible') or '')

    def test_mobile_item_form_readonly_matches_list(self):
        for name in ('product_id', 'product_uom_qty', 'product_uom_id', 'customer_lead',
                     'analytic_distribution', 'route_ids'):
            with self.subTest(field=name):
                node = self._form_node(f".//field[@name='{name}']")
                self.assertIn('combo_item_id', node.get('readonly') or '')
        for name in ('product_id', 'product_uom_id'):
            with self.subTest(field=name):
                self.assertIn('no_open', self._form_node(f".//field[@name='{name}']").get('options') or '')
        for name in ('name', 'tax_ids'):
            with self.subTest(field=name):
                self.assertNotIn('combo_item_id', self._form_node(f".//field[@name='{name}']").get('readonly') or '')

    def test_mobile_form_product_domain_excludes_combos(self):
        domain = self._form_node(".//field[@name='product_id']").get('domain') or ''
        self.assertIn("('type', '!=', 'combo')", domain)
        self.assertIn('sale_ok', domain)

    def test_mobile_package_card_shows_combo_subtotal(self):
        _order, package_line, items = self._quote()

        self.assertAlmostEqual(package_line.combo_subtotal, 600.0, places=2)
        self.assertEqual(items.mapped('combo_subtotal'), [0.0, 0.0, 0.0])

        kanban_nodes = self._direct_fields(self.mobile_kanban)
        self.assertIn('combo_subtotal', kanban_nodes)
        for name in ('price_subtotal', 'price_unit'):
            with self.subTest(field=name):
                self.assertIn("product_type == 'combo'", kanban_nodes[name].get('invisible') or '')

    def test_combo_subtotal_on_unsaved_order(self):
        order = self.orders.new({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'order_line': [
                Command.create({
                    'product_id': self.package.id, 'product_uom_qty': 1.0, 'virtual_id': 'combo-1', 'sequence': 10,
                }),
                *[Command.create({
                    'product_id': product.id,
                    'product_uom_qty': 1.0,
                    'combo_item_id': combo.combo_item_ids.filtered(lambda item: item.product_id == product).id,
                    'linked_virtual_id': 'combo-1',
                    'sequence': 11 + index,
                }) for index, (combo, product) in enumerate(
                    zip(self.combos, [self.vinilica, self.rodillo, self.brocha]))],
            ],
        })
        package_line, *items = order.order_line.sorted('sequence')

        self.assertAlmostEqual(package_line.combo_subtotal, sum(item.price_subtotal for item in items), places=2)
        self.assertAlmostEqual(package_line.combo_subtotal, 200.0, places=2)

    def test_package_rejects_price_unit(self):
        # Sin acuerdo: con precio fijo, el bloqueo de precio de la SPEC 10 ya rechazaría el cambio.
        self.env['res.partner.discount.rule'].search([('partner_id', '=', self.partner.id)]).unlink()
        _order, package_line, _items = self._quote()
        self.assertNotEqual(package_line.pricing_rule_type, 'fixed_price')

        with self.assertRaises(ValidationError):
            package_line.write({'price_unit': 50.0})

    def test_item_rejects_other_qty(self):
        _order, _package_line, items = self._quote()

        with self.assertRaises(ValidationError):
            items[0].write({'product_uom_qty': 5.0})

    def test_item_rejects_other_uom(self):
        _order, _package_line, items = self._quote()

        with self.assertRaises(ValidationError):
            items[0].write({'product_uom_id': self.env.ref('uom.product_uom_dozen').id})

    def test_item_rejects_product_change(self):
        _order, _package_line, items = self._quote()

        with self.assertRaises(ValidationError):
            items[0].write({'product_id': self.esmalte.id})

    def test_package_qty_propagates_to_items(self):
        order, package_line, items = self._quote()

        package_line.write({'product_uom_qty': 5.0})

        self.assertEqual(items.mapped('product_uom_qty'), [5.0, 5.0, 5.0])
        self.assertAlmostEqual(order.amount_untaxed, 1000.0, places=2)

    def test_mobile_save_package_qty_change(self):
        """Diálogo de la línea del paquete, onchange del pedido (copia la cantidad a sus productos)
        y `web_save` con todas las líneas que cambiaron, como el navegador."""
        order, package_line, items = self._quote()
        line_values = package_line.web_read(self.mobile_spec)[0]
        line_values['product_uom_qty'] = 5.0
        sent = {
            key: val['id'] if isinstance(val, dict) and 'id' in val else val
            for key, val in line_values.items()
            if not isinstance(val, list)
        }
        sent['order_id'] = {'id': order.id}
        line_values.update(self.lines.browse(package_line.id).onchange(
            sent, ['product_uom_qty'], self.mobile_spec).get('value', {}))
        package_vals = self._to_vals({'product_uom_qty': line_values['product_uom_qty']})
        commands = order.onchange(
            {'order_line': [Command.update(package_line.id, package_vals)]},
            ['order_line'],
            {'order_line': {'fields': self.mobile_spec}},
        )['value'].get('order_line', [])
        changed = {package_line.id: dict(package_vals)}
        for command in commands:
            if command[0] == Command.UPDATE and command[1] in (package_line | items).ids:
                changed.setdefault(command[1], {}).update(self._to_vals(command[2]))

        order.web_save(
            {'order_line': [Command.update(line_id, vals) for line_id, vals in changed.items()]}, {})

        self.assertEqual(package_line.product_uom_qty, 5.0)
        self.assertEqual(items.mapped('product_uom_qty'), [5.0, 5.0, 5.0])
        self.assertAlmostEqual(order.amount_untaxed, 1000.0, places=2)
