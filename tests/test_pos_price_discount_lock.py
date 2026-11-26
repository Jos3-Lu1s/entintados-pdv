# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPosPriceDiscountLock(TransactionCase):
    """Permiso de cada cajero para teclear precio y descuento en el Punto de Venta."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.group_price = cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
        cls.group_discount = cls.env.ref('entintados_pdv.group_sale_edit_discount')
        base_groups = 'point_of_sale.group_pos_user,sales_team.group_sale_salesman'
        cls.cashier = new_test_user(cls.env, login='pos_cashier_lock', groups=base_groups)
        cls.supervisor = new_test_user(cls.env, login='pos_supervisor_lock', groups=base_groups)
        cls.pos_config = cls.env['pos.config'].create({
            'name': 'TPV Control Precios Test',
            'company_id': cls.company.id,
        })
        cls.employee_supervisor = cls.env['hr.employee'].create({
            'name': 'Supervisor Permisos',
            'company_id': cls.company.id,
            'user_id': cls.supervisor.id,
        })
        cls.employee_cashier = cls.env['hr.employee'].create({
            'name': 'Cajero Permisos',
            'company_id': cls.company.id,
            'user_id': cls.cashier.id,
        })
        cls.employee_without_user = cls.env['hr.employee'].create({
            'name': 'Cajero Sin Usuario',
            'company_id': cls.company.id,
        })

    def _set_company_flags(self, price=True, discount=True):
        self.company.write({
            'sale_price_unit_editable': price,
            'sale_discount_editable': discount,
        })

    def _set_user_groups(self, user, price=False, discount=False):
        user.group_ids -= self.group_price | self.group_discount
        if price:
            user.group_ids |= self.group_price
        if discount:
            user.group_ids |= self.group_discount

    def _user_rights(self, user):
        Users = self.env['res.users'].with_user(user)
        record = Users._load_pos_data_read(user.with_user(user), self.pos_config)[0]
        return record['_can_edit_price'], record['_can_edit_discount']

    def _employee_rights(self, employee):
        Employees = self.env['hr.employee'].with_user(self.cashier)
        records = Employees._load_pos_data_read(employee.with_user(self.cashier), self.pos_config)
        record = next(r for r in records if r['id'] == employee.id)
        return record['_can_edit_price'], record['_can_edit_discount']

    def test_user_rights_truth_table(self):
        """Flag de compañía × grupo exento → llaves del usuario de la sesión."""
        for editable, exempt, expected in [
            (True, False, True),
            (True, True, True),
            (False, False, False),
            (False, True, True),
        ]:
            self._set_company_flags(price=editable, discount=editable)
            self._set_user_groups(self.cashier, price=exempt, discount=exempt)
            self.assertEqual(
                self._user_rights(self.cashier),
                (expected, expected),
                f"Fallo con editable={editable}, exento={exempt}",
            )

    def test_user_rights_are_independent(self):
        """El grupo de precio no da el descuento, ni al revés."""
        self._set_company_flags(price=False, discount=False)
        self._set_user_groups(self.cashier, price=True, discount=False)
        self.assertEqual(self._user_rights(self.cashier), (True, False))
        self._set_user_groups(self.cashier, price=False, discount=True)
        self.assertEqual(self._user_rights(self.cashier), (False, True))

    def test_employee_rights_follow_user(self):
        """Cada empleado lleva el permiso de su usuario, aunque lo cargue un cajero sin él."""
        self._set_company_flags(price=False, discount=False)
        self._set_user_groups(self.supervisor, price=True, discount=True)
        self._set_user_groups(self.cashier, price=False, discount=False)
        self.assertEqual(self._employee_rights(self.employee_supervisor), (True, True))
        self.assertEqual(self._employee_rights(self.employee_cashier), (False, False))

        self._set_user_groups(self.supervisor, price=True, discount=False)
        self.assertEqual(self._employee_rights(self.employee_supervisor), (True, False))

    def test_employee_without_user_uses_company(self):
        """Un empleado sin usuario solo tiene lo que deja abierto la compañía."""
        for price, discount in [(False, False), (True, False), (False, True), (True, True)]:
            self._set_company_flags(price=price, discount=discount)
            self.assertEqual(
                self._employee_rights(self.employee_without_user),
                (price, discount),
                f"Fallo con precio={price}, descuento={discount}",
            )

    def test_sale_order_edit_locked_unchanged(self):
        """El bloqueo de Ventas sigue dando lo mismo para las cuatro combinaciones."""
        for editable, exempt in [(True, False), (True, True), (False, False), (False, True)]:
            self._set_company_flags(price=editable, discount=editable)
            self._set_user_groups(self.cashier, price=exempt, discount=exempt)
            # Pedido en memoria: el cálculo solo lee la compañía y el usuario.
            order_as_cashier = self.env['sale.order'].with_user(self.cashier).new({
                'company_id': self.company.id,
            })
            locked = not (editable or exempt)
            self.assertEqual(order_as_cashier.price_unit_edit_locked, locked)
            self.assertEqual(order_as_cashier.discount_edit_locked, locked)
