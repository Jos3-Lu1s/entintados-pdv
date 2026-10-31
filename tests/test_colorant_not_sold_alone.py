# -*- coding: utf-8 -*-

from unittest.mock import patch
from uuid import uuid4

from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestColorantNotSoldAlone(TransactionCase):
    """ El colorante se compra como materia prima, pero solo sale vendido
    como línea hija de una base entintada del PDV.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.templates = cls.env['product.template']
        cls.point = cls.env.ref('entintados_pdv.uom_tint_point')
        white = cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        gallon = cls.env['tint.size'].search([('code', '=', 'G')], limit=1)

        cls.colorant = cls._create_colorant()
        cls.base = cls.templates.create({
            'name': 'Base W galón',
            'is_storable': True,
            'tint_role': 'base',
            'tint_base_type_id': white.id,
            'tint_size_id': gallon.id,
            'available_in_pos': True,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente PDV colorantes',
            'is_customer': True,
            'phone': '1234567890',
        })
        cls.config = cls.env['pos.config'].create({'name': 'Caja colorantes'})
        cls.session = cls.env['pos.session'].create({
            'config_id': cls.config.id,
            'user_id': cls.env.uid,
        })

    @classmethod
    def _create_colorant(cls, **values):
        values.setdefault('name', 'Colorante de prueba')
        values.setdefault('uom_id', cls.point.id)
        values.setdefault('is_storable', True)
        values['tint_role'] = 'colorant'
        return cls.templates.create(values)

    def _assert_not_saleable(self, template):
        self.assertFalse(template.sale_ok)
        self.assertFalse(template.available_in_pos)

    # --- Catálogo -------------------------------------------------------

    def test_colorant_created_not_saleable(self):
        colorant = self._create_colorant(sale_ok=True, available_in_pos=True)
        self._assert_not_saleable(colorant)

    def test_colorant_write_cannot_enable_sale(self):
        self.colorant.write({'sale_ok': True, 'available_in_pos': True})
        self._assert_not_saleable(self.colorant)

    def test_colorant_variant_write_cannot_enable_sale(self):
        self.colorant.product_variant_id.write({'sale_ok': True})
        self._assert_not_saleable(self.colorant)

    def test_product_becomes_colorant_turns_off_sale(self):
        product = self.templates.create({
            'name': 'Insumo vendible',
            'is_storable': True,
            'uom_id': self.point.id,
            'sale_ok': True,
            'available_in_pos': True,
        })
        product.tint_role = 'colorant'
        self._assert_not_saleable(product)

    def test_onchange_colorant_turns_off_sale(self):
        form = Form(self.templates)
        form.name = 'Colorante desde formulario'
        form.is_storable = True
        form.sale_ok = True
        form.available_in_pos = True
        form.tint_role = 'colorant'
        self.assertFalse(form.sale_ok)
        self.assertFalse(form.available_in_pos)

    def test_base_and_regular_keep_sale_flags(self):
        regular = self.templates.create({
            'name': 'Brocha',
            'sale_ok': True,
            'available_in_pos': True,
        })
        self.assertTrue(self.base.sale_ok)
        self.assertTrue(self.base.available_in_pos)
        self.assertTrue(regular.sale_ok)
        self.assertTrue(regular.available_in_pos)

    def test_colorant_purchase_ok_untouched(self):
        self.assertTrue(self._create_colorant(purchase_ok=True).purchase_ok)
        self.assertFalse(self._create_colorant(purchase_ok=False).purchase_ok)

    # --- Ventas ---------------------------------------------------------

    def test_sale_order_line_rejects_colorant(self):
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        with self.assertRaisesRegex(ValidationError, 'no se vende solo'):
            self.env['sale.order.line'].create({
                'order_id': order.id,
                'product_id': self.colorant.product_variant_id.id,
                'product_uom_qty': 10,
            })

    # --- PDV ------------------------------------------------------------

    def _line(self, template, qty, line_uuid=None):
        return (0, 0, {
            'uuid': line_uuid or str(uuid4()),
            'product_id': template.product_variant_id.id,
            'qty': qty,
            'price_unit': 0.0,
            'price_subtotal': 0.0,
            'price_subtotal_incl': 0.0,
            'tax_ids': [],
        })

    def _sync(self, lines, mapping=None):
        order = {
            'uuid': str(uuid4()),
            'session_id': self.session.id,
            'partner_id': self.partner.id,
            'state': 'draft',
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'amount_tax': 0.0,
            'amount_total': 0.0,
            'lines': lines,
            'payment_ids': [],
        }
        if mapping:
            order['relations_uuid_mapping'] = {'pos.order.line': mapping}
        return self.env['pos.order'].sync_from_ui([order])

    def test_pos_order_rejects_loose_colorant(self):
        with self.assertRaisesRegex(UserError, 'sin su base entintada'):
            self._sync([self._line(self.colorant, 24)])

    def test_pos_order_accepts_tinted_base(self):
        base_uuid, child_uuid = str(uuid4()), str(uuid4())
        self._sync(
            [self._line(self.base, 1, base_uuid),
             self._line(self.colorant, 24, child_uuid)],
            {child_uuid: {'combo_parent_id': base_uuid}},
        )
        child = self.env['pos.order.line'].search([('uuid', '=', child_uuid)])
        self.assertEqual(child.combo_parent_id.uuid, base_uuid)

    def test_pos_refund_with_colorant_not_validated(self):
        self._sync([self._line(self.colorant, -24)])
        self.assertTrue(self.env['pos.order.line'].search([
            ('product_id', '=', self.colorant.product_variant_id.id),
            ('qty', '=', -24),
        ]))


@tagged('post_install', '-at_install')
class TestReclassifyColorantWithSales(TransactionCase):
    """ Un producto que todavía puede confirmarse o facturarse en Ventas no
    puede pasar a colorante.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.point = cls.env.ref('entintados_pdv.uom_tint_point')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente reclasificación',
            'is_customer': True,
            'phone': '1234567890',
        })
        cls.product = cls.env['product.template'].create({
            'name': 'Insumo sin rol',
            'is_storable': True,
            'uom_id': cls.point.id,
            'list_price': 3.0,
            'invoice_policy': 'order',
            'sale_ok': True,
        })
        cls.variant = cls.product.product_variant_id
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        cls.env['stock.quant']._update_available_quantity(
            cls.variant, cls.warehouse.lot_stock_id, 10000)

    def _quote(self, qty=100):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'order_line': [(0, 0, {
                'product_id': self.variant.id,
                'product_uom_qty': qty,
            })],
        })

    def _confirmed(self, qty=100):
        order = self._quote(qty)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        return order

    def _assert_blocked(self, orders, record=None):
        record = record or self.product
        with self.assertRaises(ValidationError) as caught:
            record.write({'tint_role': 'colorant'})
        message = str(caught.exception)
        self.assertIn('como colorante', message)
        for order in orders:
            self.assertIn(order.name, message)
        self.assertFalse(self.product.tint_role)
        self.assertTrue(self.product.sale_ok)
        return message

    def _assert_reclassified(self):
        self.product.write({'tint_role': 'colorant'})
        self.assertEqual(self.product.tint_role, 'colorant')
        self.assertFalse(self.product.sale_ok)
        self.assertFalse(self.product.available_in_pos)

    def test_reclassify_blocked_by_quotation(self):
        self._assert_blocked(self._quote())

    def test_reclassify_blocked_by_sent_quotation(self):
        order = self._quote()
        order.write({'state': 'sent'})
        self._assert_blocked(order)

    def test_reclassify_blocked_by_confirmed_not_invoiced(self):
        self._assert_blocked(self._confirmed())

    def test_reclassify_blocked_by_partially_invoiced(self):
        order = self._confirmed()
        order._create_invoices()
        line = order.order_line
        line.product_uom_qty = 150
        self.assertLess(line.qty_invoiced, line.product_uom_qty)
        self._assert_blocked(order)

    def test_reclassify_allowed_when_fully_invoiced(self):
        order = self._confirmed()
        order._create_invoices()
        self.assertEqual(order.order_line.qty_invoiced, 100)
        self._assert_reclassified()

    def test_reclassify_allowed_when_cancelled(self):
        order = self._quote()
        order._action_cancel()
        self.assertEqual(order.state, 'cancel')
        self._assert_reclassified()

    def test_reclassify_blocked_from_variant(self):
        self._assert_blocked(self._quote(), record=self.variant)

    def test_reclassify_blocked_by_archived_variant(self):
        order = self._quote()
        self.variant.active = False
        self._assert_blocked(order)

    def test_reclassify_message_lists_orders(self):
        orders = self.env['sale.order']
        for _i in range(6):
            orders |= self._quote()
        orders = orders.sorted('name')
        message = self._assert_blocked(orders[:5])
        self.assertNotIn(orders[5].name, message)
        self.assertIn('y 1 más', message)

    def test_reclassify_without_sales_rights(self):
        order = self._quote()
        user = new_test_user(
            self.env, login='catalogo_sin_ventas',
            groups='base.group_user,product.group_product_manager')
        self.assertFalse(user.has_group('sales_team.group_sale_salesman'))
        self._assert_blocked(order, record=self.product.with_user(user))

    def test_rewrite_colorant_role_not_checked(self):
        self._assert_reclassified()
        sale_line_class = type(self.env['sale.order.line'])
        with patch.object(sale_line_class, 'search',
                          side_effect=AssertionError("no debe buscar líneas")):
            self.product.write({'tint_role': 'colorant'})
        self.assertEqual(self.product.tint_role, 'colorant')

    def _cancelled_and_reclassified(self):
        order = self._quote()
        order._action_cancel()
        self._assert_reclassified()
        # Cancelar y reclasificar son peticiones previas ya guardadas.
        self.env.flush_all()
        return order

    def _assert_still_cancelled(self, order):
        self.env.cr.execute("SELECT state FROM sale_order WHERE id = %s", [order.id])
        self.assertEqual(self.env.cr.fetchone()[0], 'cancel')

    def test_cancelled_order_cannot_be_reopened(self):
        order = self._cancelled_and_reclassified()
        with self.assertRaisesRegex(ValidationError, 'no se vende solo'):
            order.action_draft()
            # La restricción corre al recalcular el `state` de la línea, al cerrar la petición.
            self.env.flush_all()
        self._assert_still_cancelled(order)

    def test_order_state_write_rechecks_colorant(self):
        order = self._cancelled_and_reclassified()
        with self.assertRaisesRegex(ValidationError, 'no se vende solo'):
            order.write({'state': 'draft'})
            # La restricción corre al recalcular el `state` de la línea, al cerrar la petición.
            self.env.flush_all()
        self._assert_still_cancelled(order)
