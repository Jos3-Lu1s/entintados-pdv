# -*- coding: utf-8 -*-

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestCommercialAgreementsAccess(TransactionCase):
    """Solo «Gestionar acuerdos comerciales» cambia el descuento y los acuerdos del cliente."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Facturación: `l10n_mx_edi` exige acceso a las adendas para duplicar un contacto.
        base_groups = 'base.group_user,sales_team.group_sale_salesman,base.group_partner_manager,' \
            'product.group_product_pricelist,sale.group_discount_per_so_line,account.group_account_invoice'
        cls.salesman = new_test_user(cls.env, login='spec17_salesman', groups=base_groups)
        cls.agreements_manager = new_test_user(
            cls.env, login='spec17_agreements',
            groups=base_groups + ',entintados_pdv.group_sale_commercial_agreements')
        cls.cashier = new_test_user(
            cls.env, login='spec17_cashier', groups='base.group_user,point_of_sale.group_pos_user')
        cls.product = cls.env['product.template'].create({
            'name': 'Brocha Acuerdos', 'list_price': 100.0,
        }).product_variant_ids[0]
        cls.other_product = cls.env['product.template'].create({
            'name': 'Rodillo Acuerdos', 'list_price': 200.0,
        }).product_variant_ids[0]
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Acuerdos', 'is_customer': True, 'discount': 0.10, 'phone': '5512345678',
        })
        cls.partner.property_product_pricelist = cls.env['product.pricelist'].create({'name': 'Tarifa Acuerdos'})
        cls.rule = cls.env['res.partner.discount.rule'].create({
            'partner_id': cls.partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': cls.product.id,
            'discount': 5.0,
        })

    def _fixed_price_rule_values(self, price=1.0):
        # Producto sin regla: así solo el permiso puede rechazar el acuerdo, no la regla duplicada.
        return {
            'partner_id': self.partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': self.other_product.id,
            'fixed_price': price,
        }

    def test_rules_read_only_without_group(self):
        """Sin el grupo, las reglas se leen pero no se crean, cambian ni borran."""
        rules = self.env['res.partner.discount.rule'].with_user(self.salesman)
        self.assertEqual(rules.search([('partner_id', '=', self.partner.id)]), self.rule)
        self.assertEqual(self.rule.with_user(self.salesman).discount, 5.0)
        with self.assertRaises(AccessError):
            rules.create(self._fixed_price_rule_values())
        with self.assertRaises(AccessError):
            self.rule.with_user(self.salesman).write({'discount': 50.0})
        with self.assertRaises(AccessError):
            self.rule.with_user(self.salesman).unlink()
        self.assertTrue(self.rule.exists())
        self.assertEqual(self.rule.discount, 5.0)

    def test_partner_discount_protected_without_group(self):
        """Sin el grupo, el descuento del cliente no cambia, pero reenviar el mismo valor sí pasa."""
        partner = self.partner.with_user(self.salesman)
        with self.assertRaises(AccessError):
            partner.write({'discount': 0.50})
        self.assertEqual(self.partner.discount, 0.10)
        partner.write({'discount': 0.10})
        partner.write({'name': 'Cliente Acuerdos Renombrado'})
        self.assertEqual(self.partner.discount, 0.10)

    def test_partner_create_without_group(self):
        """Sin el grupo, se crean contactos sin descuento; con descuento, no."""
        partners = self.env['res.partner'].with_user(self.salesman)
        self.assertEqual(partners.create({'name': 'Nuevo sin descuento', 'discount': 0.0}).discount, 0.0)
        self.assertEqual(partners.create({'name': 'Nuevo sin campo'}).discount, 0.0)
        with self.assertRaises(AccessError):
            partners.create({'name': 'Nuevo con descuento', 'discount': 0.10})

    def test_partner_copy_drops_discount_without_group(self):
        """Duplicar sin el grupo deja la copia sin descuento; con el grupo lo conserva."""
        self.assertEqual(self.partner.with_user(self.salesman).copy().discount, 0.0)
        self.assertEqual(self.partner.with_user(self.agreements_manager).copy().discount, 0.10)

    def test_group_manages_agreements(self):
        """Con el grupo, se crean, cambian y borran reglas y se cambia el descuento del cliente."""
        rules = self.env['res.partner.discount.rule'].with_user(self.agreements_manager)
        new_rule = rules.create(self._fixed_price_rule_values(price=70.0))
        self.rule.with_user(self.agreements_manager).write({'discount': 7.0})
        self.assertEqual(self.rule.discount, 7.0)
        new_rule.unlink()
        self.assertFalse(new_rule.exists())
        self.partner.with_user(self.agreements_manager).write({'discount': 0.15})
        self.assertEqual(self.partner.discount, 0.15)

    def test_partner_form_readonly_without_group(self):
        """En la ficha, descuento y acuerdos son de solo lectura sin el grupo y editables con él."""
        with self.subTest('sin el grupo'):
            partner_form = Form(self.partner.with_user(self.salesman))
            self.assertFalse(partner_form.commercial_agreements_editable)
            with self.assertRaises(AssertionError):
                partner_form.discount = 0.20
            with self.assertRaises(AssertionError):
                partner_form.discount_rule_ids.new()

        with self.subTest('con el grupo'):
            with Form(self.partner.with_user(self.agreements_manager)) as partner_form:
                self.assertTrue(partner_form.commercial_agreements_editable)
                partner_form.discount = 0.20
                with partner_form.discount_rule_ids.edit(0) as rule_form:
                    rule_form.discount = 8.0
            self.assertEqual(self.partner.discount, 0.20)
            self.assertEqual(self.rule.discount, 8.0)

    def test_locked_salesman_cannot_set_fixed_price_agreement(self):
        """Con precio y descuento bloqueados, el vendedor no se pone un precio fijo desde el cliente."""
        self.env.company.write({'sale_price_unit_editable': False, 'sale_discount_editable': False})
        partner = self.partner.with_user(self.salesman)
        with self.assertRaises(AccessError):
            self.env['res.partner.discount.rule'].with_user(self.salesman).create(self._fixed_price_rule_values())
        with self.assertRaises(AccessError):
            partner.write({'discount_rule_ids': [Command.create(self._fixed_price_rule_values())]})
        with self.assertRaises(AccessError):
            partner.write({'discount': 0.50})
        self.assertEqual(self.partner.discount_rule_ids, self.rule)

        order = self.env['sale.order'].with_user(self.salesman).create({
            'partner_id': self.partner.id,
            'user_id': self.salesman.id,
            'order_line': [Command.create({'product_id': self.other_product.id, 'product_uom_qty': 1.0})],
        })
        line = order.order_line
        self.assertEqual(line.price_unit, 200.0)
        self.assertNotEqual(line.pricing_rule_type, 'fixed_price')
        self.assertEqual(line.discount, 10.0)

    def test_pos_cashier_loads_agreements(self):
        """Un cajero sin el grupo sigue cargando los acuerdos del cliente en el PDV."""
        pos_config = self.env['pos.config'].create({'name': 'PDV Acuerdos'})
        loaded = self.env['res.partner.discount.rule'].with_user(self.cashier)._load_pos_data_search_read({}, pos_config)
        loaded_rule = next((rule for rule in loaded if rule['id'] == self.rule.id), None)
        self.assertTrue(loaded_rule)
        self.assertEqual(loaded_rule['discount'], 5.0)
        self.assertEqual(loaded_rule['partner_id'], self.partner.id)
