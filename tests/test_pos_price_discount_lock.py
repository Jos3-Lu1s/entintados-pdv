# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPosPriceDiscountLock(TransactionCase):
    """Validación de permisos de edición de precio unitario y descuento en el Punto de Venta."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.group_price = cls.env.ref('entintados_pdv.group_sale_edit_price_unit')
        cls.group_discount = cls.env.ref('entintados_pdv.group_sale_edit_discount')
        base_groups = 'point_of_sale.group_pos_user,sales_team.group_sale_salesman'
        cls.cashier = new_test_user(cls.env, login='pos_cashier_lock', groups=base_groups)
        cls.pos_config = cls.env['pos.config'].create({
            'name': 'TPV Control Precios Test',
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

    def test_pos_config_user_permissions_truth_table(self):
        """Verifica el cálculo de user_can_edit_price y user_can_edit_discount en pos.config."""
        pos_config = self.pos_config.with_user(self.cashier)

        # Matriz para precio: (company_editable, has_group, expected_can_edit)
        for editable, exempt, expected in [
            (True, False, True),
            (True, True, True),
            (False, False, False),
            (False, True, True),
        ]:
            self._set_company_flags(price=editable, discount=True)
            self._set_user_groups(self.cashier, price=exempt, discount=False)
            pos_config.invalidate_recordset(['user_can_edit_price', 'sale_price_unit_editable'])
            self.assertEqual(
                pos_config.user_can_edit_price,
                expected,
                f"Fallo en precio con editable={editable}, exempt={exempt}",
            )

        # Matriz para descuento: (company_editable, has_group, expected_can_edit)
        for editable, exempt, expected in [
            (True, False, True),
            (True, True, True),
            (False, False, False),
            (False, True, True),
        ]:
            self._set_company_flags(price=True, discount=editable)
            self._set_user_groups(self.cashier, price=False, discount=exempt)
            pos_config.invalidate_recordset(['user_can_edit_discount', 'sale_discount_editable'])
            self.assertEqual(
                pos_config.user_can_edit_discount,
                expected,
                f"Fallo en descuento con editable={editable}, exempt={exempt}",
            )

    def test_load_pos_data_fields_and_read(self):
        """Verifica que los campos de gobernanza viajen en el payload inicial del TPV."""
        pos_config = self.pos_config.with_user(self.cashier)
        self._set_company_flags(price=False, discount=False)
        self._set_user_groups(self.cashier, price=False, discount=True)

        fields_list = pos_config._load_pos_data_fields(pos_config)
        self.assertIn('user_can_edit_price', fields_list)
        self.assertIn('user_can_edit_discount', fields_list)
        self.assertIn('sale_price_unit_editable', fields_list)
        self.assertIn('sale_discount_editable', fields_list)

        read_records = pos_config._load_pos_data_read(pos_config, pos_config)
        self.assertTrue(read_records)
        record_data = read_records[0]
        self.assertFalse(record_data['user_can_edit_price'])
        self.assertTrue(record_data['user_can_edit_discount'])
        self.assertFalse(record_data['sale_price_unit_editable'])
        self.assertFalse(record_data['sale_discount_editable'])
