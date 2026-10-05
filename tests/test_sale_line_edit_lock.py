# -*- coding: utf-8 -*-

from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.tests import Form
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestSaleLineEditLock(TransactionCase):
    """Bloqueo de la edición de `price_unit` y `discount` desde Ajustes de Ventas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.group_price = cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
        cls.group_discount = cls.env.ref('entintados_pdv.group_sale_edit_discount')
        base_groups = 'sales_team.group_sale_salesman,product.group_product_pricelist,' \
            'sale.group_discount_per_so_line,uom.group_uom'
        cls.salesman = new_test_user(cls.env, login='spec10_salesman', groups=base_groups)
        cls.manager = new_test_user(
            cls.env, login='spec10_manager',
            groups=base_groups.replace('group_sale_salesman', 'group_sale_manager'))
        cls.product = cls.env['product.template'].create({
            'name': 'Brocha Bloqueo', 'list_price': 100.0,
        }).product_variant_ids[0]
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'Tarifa Bloqueo'})
        cls.partner = cls._create_partner('Cliente Bloqueo', cls.pricelist)

    @classmethod
    def _create_partner(cls, name, pricelist, rule=None):
        partner = cls.env['res.partner'].create({'name': name, 'is_customer': True, 'discount': 0.0})
        partner.property_product_pricelist = pricelist
        if rule:
            cls.env['res.partner.discount.rule'].create(dict(rule, partner_id=partner.id, applied_on='0_product'))
        return partner

    def _set_company_flags(self, company=None, price=True, discount=True):
        (company or self.company).write({
            'sale_price_unit_editable': price,
            'sale_discount_editable': discount,
        })

    def _set_exempt_groups(self, user, price=False, discount=False):
        user.group_ids -= self.group_price | self.group_discount
        if price:
            user.group_ids |= self.group_price
        if discount:
            user.group_ids |= self.group_discount

    def _create_order(self, user, partner=None, product=None, company=None):
        values = {
            'partner_id': (partner or self.partner).id,
            'user_id': user.id,
            'order_line': [(0, 0, {'product_id': (product or self.product).id, 'product_uom_qty': 1.0})],
        }
        if company:
            values['company_id'] = company.id
        return self.env['sale.order'].with_company(company or self.company).create(values)

    def _locks(self, order, user):
        order = order.with_user(user)
        order.invalidate_recordset(['price_unit_edit_locked', 'discount_edit_locked'])
        return order.price_unit_edit_locked, order.discount_edit_locked

    def test_lock_flags_truth_table(self):
        """Cada fila de la tabla de verdad, por campo, y el Administrador de ventas sin grupo."""
        for user in (self.salesman, self.manager):
            order = self._create_order(user)
            for field_name in ('price_unit', 'discount'):
                for editable, exempt, locked in (
                    (True, False, False),
                    (True, True, False),
                    (False, False, True),
                    (False, True, False),
                ):
                    with self.subTest(user=user.login, field=field_name, editable=editable, exempt=exempt):
                        is_price = field_name == 'price_unit'
                        # El otro campo queda bloqueado y sin grupo: no debe afectar a este.
                        self._set_company_flags(
                            price=editable if is_price else False,
                            discount=False if is_price else editable,
                        )
                        self._set_exempt_groups(user, price=is_price and exempt, discount=not is_price and exempt)
                        price_locked, discount_locked = self._locks(order, user)
                        self.assertEqual(price_locked if is_price else discount_locked, locked)
                        self.assertTrue(discount_locked if is_price else price_locked)

    def test_lock_uses_order_company(self):
        """El bloqueo sigue a la compañía del pedido, no a la compañía activa del usuario."""
        other_company = self.env['res.company'].create({'name': 'Compañía Bloqueo 2', 'phone': '1234567890'})
        self.env.user.company_ids |= other_company
        self.salesman.company_ids |= other_company
        self._set_exempt_groups(self.salesman)
        self._set_company_flags(price=True, discount=True)
        self._set_company_flags(other_company, price=False, discount=False)

        order_main = self._create_order(self.salesman)
        order_other = self._create_order(self.salesman, company=other_company)
        self.assertEqual(self.salesman.company_id, self.company)
        self.assertEqual(self._locks(order_main, self.salesman), (False, False))
        self.assertEqual(self._locks(order_other, self.salesman), (True, True))

    def test_form_readonly_when_locked(self):
        """Sin grupo, las celdas de la línea son de solo lectura; con el grupo, se editan."""
        self._set_company_flags(price=False, discount=False)

        with self.subTest('vendedor sin grupo'):
            self._set_exempt_groups(self.salesman)
            order = self._create_order(self.salesman)
            with Form(order.with_user(self.salesman)) as order_form:
                with order_form.order_line.edit(0) as line_form:
                    with self.assertRaises(AssertionError):
                        line_form.price_unit = 80.0
                    with self.assertRaises(AssertionError):
                        line_form.discount = 7.0
            self.assertEqual(order.order_line.price_unit, 100.0)
            self.assertEqual(order.order_line.discount, 0.0)

        with self.subTest('vendedor con los dos grupos'):
            self._set_exempt_groups(self.salesman, price=True, discount=True)
            order = self._create_order(self.salesman)
            with Form(order.with_user(self.salesman)) as order_form:
                with order_form.order_line.edit(0) as line_form:
                    line_form.price_unit = 80.0
                    line_form.discount = 7.0
            self.assertEqual(order.order_line.price_unit, 80.0)
            self.assertEqual(order.order_line.discount, 7.0)

        with self.subTest('formulario de línea (móvil)'):
            arch = self.env['sale.order'].with_user(self.salesman).get_views([(False, 'form')])['views']['form']['arch']
            line_form_view = etree.fromstring(arch).xpath("//field[@name='order_line']/form")[0]
            for field_name, lock_field in (('price_unit', 'price_unit_edit_locked'), ('discount', 'discount_edit_locked')):
                node = line_form_view.xpath(f".//field[@name='{field_name}']")[0]
                self.assertIn(f'parent.{lock_field}', node.get('readonly') or '')

    def _setup_other_currency(self, rate=17.0):
        """Moneda O distinta de la de la compañía, con `1 C = rate O`. Devuelve (C, O)."""
        company_currency = self.company.currency_id
        usd, mxn = self.env.ref('base.USD'), self.env.ref('base.MXN')
        other = mxn if company_currency == usd else usd
        other.active = True
        self.env['res.currency.rate'].search([
            ('currency_id', 'in', (company_currency | other).ids),
            ('company_id', 'in', (self.company.id, False)),
        ]).unlink()
        self.env['res.currency.rate'].create({
            'name': fields.Date.today() - timedelta(days=30),
            'currency_id': other.id,
            'company_id': self.company.id,
            'rate': rate,
        })
        return company_currency, other

    def _screen_values(self, line_form):
        return (round(line_form.price_unit, 2), round(line_form.discount, 2),
                line_form.pricing_rule_type, line_form.price_origin)

    def _db_values(self, line):
        return (round(line.price_unit, 2), round(line.discount, 2), line.pricing_rule_type, line.price_origin)

    def test_locked_form_save_matches_screen(self):
        """Con bloqueo y sin grupo, lo que se guarda coincide con la pantalla y nada queda manual."""
        self._set_company_flags(price=False, discount=False)
        self._set_exempt_groups(self.salesman)
        company_currency, other = self._setup_other_currency()
        pricelist_c = self.env['product.pricelist'].create({'name': 'T-C Bloqueo', 'currency_id': company_currency.id})
        pricelist_o = self.env['product.pricelist'].create({'name': 'T-O Bloqueo', 'currency_id': other.id})
        pack_6 = self.env.ref('uom.product_uom_pack_6')
        product_b = self.env['product.template'].create({
            'name': 'Rodillo Bloqueo', 'list_price': 200.0,
        }).product_variant_ids[0]
        agreement_10 = {'rule_type': 'discount', 'product_id': self.product.id, 'discount': 10.0}
        partner_10 = self._create_partner('Cliente Acuerdo 10', pricelist_c, agreement_10)
        partner_15 = self._create_partner('Cliente Acuerdo 15', pricelist_c, dict(agreement_10, discount=15.0))
        partner_fixed = self._create_partner('Cliente Fijo Bloqueo', pricelist_c, {
            'rule_type': 'fixed_price', 'product_id': self.product.id, 'fixed_price': 70.0,
        })

        scenarios = (
            ('producto', partner_10, {}, {'product_id': product_b}),
            ('cantidad', partner_10, {}, {'product_uom_qty': 3.0}),
            ('UdM', partner_fixed, {}, {'product_uom_id': pack_6}),
            ('tarifa en otra moneda', partner_fixed, {'pricelist_id': pricelist_o}, {}),
            ('cliente', partner_10, {'partner_id': partner_15}, {}),
        )
        for label, partner, order_values, line_values in scenarios:
            with self.subTest(label):
                order = self._create_order(self.salesman, partner=partner)
                with Form(order.with_user(self.salesman)) as order_form:
                    for field_name, value in order_values.items():
                        setattr(order_form, field_name, value)
                    with order_form.order_line.edit(0) as line_form:
                        for field_name, value in line_values.items():
                            setattr(line_form, field_name, value)
                        screen = self._screen_values(line_form)
                line = order.order_line
                self.assertEqual(self._db_values(line), screen)
                self.assertNotEqual(line.pricing_rule_type, 'manual')

    def test_discount_button_hidden_when_locked(self):
        """Los dos botones "Descuento" se ocultan cuando el descuento está bloqueado."""
        arch = self.env['sale.order'].with_user(self.salesman).get_views([(False, 'form')])['views']['form']['arch']
        buttons = etree.fromstring(arch).xpath("//button[@name='action_open_discount_wizard']")
        self.assertEqual(len(buttons), 2)
        for button in buttons:
            self.assertIn('discount_edit_locked', button.get('invisible') or '')
