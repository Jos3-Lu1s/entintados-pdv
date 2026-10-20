# -*- coding: utf-8 -*-

from lxml import etree

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
