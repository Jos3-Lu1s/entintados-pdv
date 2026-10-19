# -*- coding: utf-8 -*-

import psycopg2

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestPartnerMergeAgreements(TransactionCase):
    """Fusionar contactos no deja reglas duplicadas: en cada objetivo repetido gana el destino."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rule = cls.env['res.partner.discount.rule']
        cls.product = cls.env['product.template'].create({
            'name': 'Brocha Fusión', 'list_price': 100.0,
        }).product_variant_ids[0]
        base_groups = 'base.group_user,base.group_partner_manager'
        cls.partner_manager = new_test_user(cls.env, login='spec18_partner_manager', groups=base_groups)
        cls.agreements_manager = new_test_user(
            cls.env, login='spec18_agreements',
            groups=base_groups + ',entintados_pdv.group_sale_commercial_agreements')

    def _partner(self, name, discount=0.0):
        return self.env['res.partner'].create({'name': name, 'is_customer': True, 'discount': discount})

    def _product(self, name, line=False):
        return self.env['product.template'].create({
            'name': name, 'list_price': 100.0, 'lines_product_id': line and line.id,
        }).product_variant_ids[0]

    def _discount_rule(self, partner, product, discount):
        return self.Rule.create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'discount',
            'product_id': product.id,
            'discount': discount,
        })

    def _fixed_price_rule(self, partner, product, price):
        return self.Rule.create({
            'partner_id': partner.id,
            'applied_on': '0_product',
            'rule_type': 'fixed_price',
            'product_id': product.id,
            'fixed_price': price,
        })

    def _merge(self, partners, dst_partner, user=None):
        wizard = self.env['base.partner.merge.automatic.wizard']
        if user:
            wizard = wizard.with_user(user)
        wizard._merge(partners.ids, dst_partner.with_env(wizard.env))
        self.env.invalidate_all()

    def test_merge_destination_rule_wins(self):
        """A (10 %) se fusiona en B (20 %): B queda con una sola regla en P y aplica 20 %."""
        partner_a = self._partner('Cliente A Fusión')
        partner_b = self._partner('Cliente B Fusión')
        self._discount_rule(partner_a, self.product, 10.0)
        rule_b = self._discount_rule(partner_b, self.product, 20.0)

        self._merge(partner_a | partner_b, partner_b)

        self.assertFalse(partner_a.exists())
        self.assertEqual(partner_b.discount_rule_ids, rule_b)
        self.assertEqual(partner_b._get_partner_pricing_rule(self.product)['discount'], 20.0)

    def test_merge_keeps_union_of_rules(self):
        """Las reglas del origen sin conflicto pasan al destino; en los objetivos repetidos manda el destino."""
        scheme = self.env['tint.schema'].create({'name': 'Esquema Fusión'})
        line = self.env['lines.product'].create({'name': 'Línea Fusión', 'scheme': scheme.id})
        p1 = self._product('P1 Fusión', line)
        p2 = self._product('P2 Fusión')
        p3 = self._product('P3 Fusión')
        p4 = self._product('P4 Fusión')
        p5 = self._product('P5 Fusión', line)
        partner_a = self._partner('Cliente A Unión')
        partner_b = self._partner('Cliente B Unión')
        self._discount_rule(partner_a, p1, 10.0)
        self._fixed_price_rule(partner_a, p2, 70.0)
        self._discount_rule(partner_a, p3, 15.0)
        self._fixed_price_rule(partner_a, p4, 50.0)
        self.Rule.create({
            'partner_id': partner_a.id, 'applied_on': '1_line', 'rule_type': 'discount',
            'line_id': line.id, 'discount': 8.0,
        })
        rules_b = (
            self._discount_rule(partner_b, p1, 20.0)
            | self._discount_rule(partner_b, p2, 5.0)
            | self._discount_rule(partner_b, p3, 15.0)
            | self.Rule.create({
                'partner_id': partner_b.id, 'applied_on': '2_scheme', 'rule_type': 'discount',
                'scheme_id': scheme.id, 'discount': 12.0,
            })
        )

        self._merge(partner_a | partner_b, partner_b)

        self.assertLessEqual(rules_b, partner_b.discount_rule_ids)
        self.assertEqual(
            {(r.applied_on, r.product_id, r.line_id, r.scheme_id, r.rule_type, r.discount, r.fixed_price)
             for r in partner_b.discount_rule_ids},
            {
                ('0_product', p1, self.env['lines.product'], self.env['tint.schema'], 'discount', 20.0, 0.0),
                ('0_product', p2, self.env['lines.product'], self.env['tint.schema'], 'discount', 5.0, 0.0),
                ('0_product', p3, self.env['lines.product'], self.env['tint.schema'], 'discount', 15.0, 0.0),
                ('0_product', p4, self.env['lines.product'], self.env['tint.schema'], 'fixed_price', 0.0, 50.0),
                ('1_line', self.env['product.product'], line, self.env['tint.schema'], 'discount', 8.0, 0.0),
                ('2_scheme', self.env['product.product'], self.env['lines.product'], scheme, 'discount', 12.0, 0.0),
            },
        )
        # La regla por producto del destino y la regla por línea del origen conviven.
        self.assertEqual(partner_b._get_partner_pricing_rule(p1)['discount'], 20.0)
        self.assertEqual(partner_b._get_partner_pricing_rule(p5)['discount'], 8.0)
        self.assertEqual(partner_b._get_partner_pricing_rule(p2)['type'], 'discount')
        self.assertEqual(partner_b._get_partner_pricing_rule(p4)['price'], 50.0)

    def test_merge_blocked_when_sources_conflict(self):
        """Dos orígenes con regla en P y el destino sin ella: la fusión falla sin tocar nada."""
        partner_a = self._partner('Cliente A Orígenes')
        partner_b = self._partner('Cliente B Orígenes')
        partner_c = self._partner('Cliente C Orígenes')
        rule_a = self._discount_rule(partner_a, self.product, 10.0)
        rule_c = self._discount_rule(partner_c, self.product, 15.0)

        with self.assertRaisesRegex(UserError, 'Brocha Fusión.*Cliente A Orígenes.*Cliente C Orígenes'):
            self._merge(partner_a | partner_b | partner_c, partner_b)

        partners = partner_a | partner_b | partner_c
        self.assertEqual(partners.exists(), partners)
        self.assertEqual(partner_a.discount_rule_ids, rule_a)
        self.assertEqual(partner_c.discount_rule_ids, rule_c)
        self.assertFalse(partner_b.discount_rule_ids)

    def test_merge_sources_conflict_resolved_by_destination(self):
        """Dos orígenes con regla en P y el destino también: gana el destino."""
        partner_a = self._partner('Cliente A Tres')
        partner_b = self._partner('Cliente B Tres')
        partner_c = self._partner('Cliente C Tres')
        self._discount_rule(partner_a, self.product, 10.0)
        self._discount_rule(partner_c, self.product, 15.0)
        rule_b = self._discount_rule(partner_b, self.product, 20.0)

        self._merge(partner_a | partner_b | partner_c, partner_b)

        self.assertFalse((partner_a | partner_c).exists())
        self.assertEqual(partner_b.discount_rule_ids, rule_b)

    def test_merge_source_rules_require_group(self):
        """Sin el grupo de acuerdos, un origen con reglas no se fusiona y nada cambia."""
        partner_a = self._partner('Cliente A Permiso')
        partner_b = self._partner('Cliente B Permiso')
        rule_a = self._discount_rule(partner_a, self.product, 10.0)

        with self.assertRaisesRegex(AccessError, 'pueden fusionar contactos'):
            self._merge(partner_a | partner_b, partner_b, user=self.partner_manager)

        self.assertTrue(partner_a.exists())
        self.assertEqual(partner_a.discount_rule_ids, rule_a)
        self.assertFalse(partner_b.discount_rule_ids)

    def test_merge_source_discount_requires_group(self):
        """Sin el grupo de acuerdos, un origen con descuento global no se fusiona y nada cambia."""
        partner_a = self._partner('Cliente A Descuento', discount=0.10)
        partner_b = self._partner('Cliente B Descuento')

        with self.assertRaisesRegex(AccessError, 'pueden fusionar contactos'):
            self._merge(partner_a | partner_b, partner_b, user=self.partner_manager)

        self.assertTrue(partner_a.exists())
        self.assertEqual(partner_a.discount, 0.10)
        self.assertEqual(partner_b.discount, 0.0)

    def test_merge_source_without_agreements_allowed_without_group(self):
        """Sin el grupo, un origen sin acuerdos se fusiona y el destino conserva sus reglas."""
        partner_a = self._partner('Cliente A Sin Acuerdos')
        partner_b = self._partner('Cliente B Con Acuerdos', discount=0.05)
        rule_b = self._discount_rule(partner_b, self.product, 20.0)

        self._merge(partner_a | partner_b, partner_b, user=self.partner_manager)

        self.assertFalse(partner_a.exists())
        self.assertEqual(partner_b.discount_rule_ids, rule_b)
        self.assertEqual(partner_b.discount, 0.05)

    def test_merge_with_group_destination_wins(self):
        """Con el grupo de acuerdos, un origen con reglas en conflicto se fusiona y gana el destino."""
        partner_a = self._partner('Cliente A Con Grupo')
        partner_b = self._partner('Cliente B Con Grupo')
        self._discount_rule(partner_a, self.product, 10.0)
        rule_b = self._discount_rule(partner_b, self.product, 20.0)

        self._merge(partner_a | partner_b, partner_b, user=self.agreements_manager)

        self.assertFalse(partner_a.exists())
        self.assertEqual(partner_b.discount_rule_ids, rule_b)

    def test_merge_logs_discarded_rules(self):
        """El chatter del destino lista la regla descartada y el contacto del que venía."""
        partner_a = self._partner('Cliente A Rastro')
        partner_b = self._partner('Cliente B Rastro')
        rule_a = self._discount_rule(partner_a, self.product, 10.0)
        rule_name = rule_a.name
        self._discount_rule(partner_b, self.product, 20.0)

        self._merge(partner_a | partner_b, partner_b)

        body = partner_b.message_ids[0].body
        self.assertIn('Acuerdos comerciales descartados', body)
        self.assertIn(rule_name, body)
        self.assertIn('Cliente A Rastro', body)

    def test_merge_without_discarded_rules_logs_nothing(self):
        """Sin reglas descartadas no se publica el mensaje de acuerdos descartados."""
        partner_a = self._partner('Cliente A Sin Rastro')
        partner_b = self._partner('Cliente B Sin Rastro')
        self._discount_rule(partner_a, self.product, 10.0)

        self._merge(partner_a | partner_b, partner_b)

        self.assertFalse(any(
            'Acuerdos comerciales descartados' in (message.body or '') for message in partner_b.message_ids
        ))

    def _insert_rule_sql(self, partner, applied_on, target_column, target_id):
        self.env.cr.execute(
            f"""INSERT INTO res_partner_discount_rule (partner_id, applied_on, rule_type, {target_column}, discount)
                VALUES (%s, %s, 'discount', %s, 5.0)""",
            (partner.id, applied_on, target_id),
        )

    def test_sql_duplicate_rules_rejected(self):
        """Una segunda regla del mismo cliente y objetivo insertada por SQL viola la unicidad."""
        scheme = self.env['tint.schema'].create({'name': 'Esquema Índice'})
        line = self.env['lines.product'].create({'name': 'Línea Índice', 'scheme': scheme.id})
        partner = self._partner('Cliente Índice')
        for applied_on, column, target in (
            ('0_product', 'product_id', self.product),
            ('1_line', 'line_id', line),
            ('2_scheme', 'scheme_id', scheme),
        ):
            with self.subTest(applied_on=applied_on):
                self._insert_rule_sql(partner, applied_on, column, target.id)
                with mute_logger('odoo.sql_db'), self.assertRaises(psycopg2.errors.UniqueViolation),                         self.env.cr.savepoint():
                    self._insert_rule_sql(partner, applied_on, column, target.id)

    def test_product_and_line_rules_coexist(self):
        """Una regla por producto y otra por línea del mismo cliente no chocan con los índices."""
        scheme = self.env['tint.schema'].create({'name': 'Esquema Convivencia'})
        line = self.env['lines.product'].create({'name': 'Línea Convivencia', 'scheme': scheme.id})
        partner = self._partner('Cliente Convivencia')
        product_rule = self._discount_rule(partner, self.product, 10.0)
        line_rule = self.Rule.create({
            'partner_id': partner.id, 'applied_on': '1_line', 'rule_type': 'discount',
            'line_id': line.id, 'discount': 8.0,
        })

        self.assertEqual(partner.discount_rule_ids, product_rule | line_rule)

    def test_orm_duplicate_rule_keeps_readable_message(self):
        """Con los índices puestos, el duplicado por ORM sigue dando el mensaje que nombra producto y cliente."""
        other_product = self._product('Rodillo Fusión')
        partner = self._partner('Cliente Mensaje')
        self._discount_rule(partner, self.product, 10.0)
        other_rule = self._discount_rule(partner, other_product, 5.0)
        message = 'Ya existe una regla comercial para el producto "Brocha Fusión" en el cliente "Cliente Mensaje"'

        with self.assertRaisesRegex(ValidationError, message):
            self._discount_rule(partner, self.product, 20.0)
        with self.assertRaisesRegex(ValidationError, message):
            other_rule.write({'product_id': self.product.id})

    def test_orm_duplicate_rules_in_same_batch(self):
        """Dos reglas iguales en el mismo create dan el mensaje legible, no el error del índice."""
        partner = self._partner('Cliente Lote')
        vals = {
            'partner_id': partner.id, 'applied_on': '0_product', 'rule_type': 'discount',
            'product_id': self.product.id, 'discount': 10.0,
        }

        with self.assertRaisesRegex(ValidationError, 'Ya existe una regla comercial para el producto "Brocha Fusión"'):
            self.Rule.create([vals, dict(vals, discount=20.0)])
