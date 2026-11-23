# -*- coding: utf-8 -*-

from uuid import uuid4

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPosPricingPersistence(TransactionCase):
    """El pedido del PDV guarda su acuerdo, su promoción y el origen de su precio, y el
    servidor rechaza las combinaciones que no pueden darse en una línea."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls._create_customer('Cliente PDV precios')
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Tarifa PDV precios',
            'currency_id': cls.company.currency_id.id,
            'company_id': cls.company.id,
        })
        cls.config = cls.env['pos.config'].create({
            'name': 'Caja precios',
            'pricelist_id': cls.pricelist.id,
        })
        cls.session = cls.env['pos.session'].create({
            'config_id': cls.config.id,
            'user_id': cls.env.uid,
            'state': 'opened',
        })
        cls.product = cls._create_product('Esmalte PDV precios')
        cls.other_product = cls._create_product('Brocha PDV precios')

        white = cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        gallon = cls.env['tint.size'].search([('code', '=', 'G')], limit=1)
        cls.base = cls.env['product.template'].create({
            'name': 'Base W galón precios',
            'is_storable': True,
            'tint_role': 'base',
            'tint_base_type_id': white.id,
            'tint_size_id': gallon.id,
            'available_in_pos': True,
        }).product_variant_id
        cls.colorant = cls.env['product.template'].create({
            'name': 'Colorante precios',
            'uom_id': cls.env.ref('entintados_pdv.uom_tint_point').id,
            'is_storable': True,
            'tint_role': 'colorant',
        }).product_variant_id

        cls.program = cls.env['loyalty.program'].create({
            'name': 'Promoción PDV precios',
            'program_type': 'promotion',
            'applies_on': 'current',
        })
        cls.order_reward = cls.env['loyalty.reward'].create({
            'program_id': cls.program.id,
            'reward_type': 'discount',
            'discount': 20.0,
            'discount_mode': 'percent',
            'discount_applicability': 'order',
        })
        cls.line_reward = cls.env['loyalty.reward'].create({
            'program_id': cls.program.id,
            'reward_type': 'discount',
            'discount': 15.0,
            'discount_mode': 'percent',
            'discount_applicability': 'specific',
            'discount_product_ids': [Command.set(cls.product.ids)],
        })
        cls.amount_reward = cls.env['loyalty.reward'].create({
            'program_id': cls.program.id,
            'reward_type': 'discount',
            'discount': 50.0,
            'discount_mode': 'per_order',
            'discount_applicability': 'order',
        })

    @classmethod
    def _create_customer(cls, name):
        return cls.env['res.partner'].create({
            'name': name,
            'is_customer': True,
            'phone': '5551234567',
        })

    @classmethod
    def _create_product(cls, name):
        return cls.env['product.product'].create({
            'name': name,
            'list_price': 100.0,
            'type': 'consu',
            'available_in_pos': True,
            'taxes_id': [Command.clear()],
        })

    def _line(self, product, line_uuid=None, **values):
        return (0, 0, {
            'uuid': line_uuid or str(uuid4()),
            'product_id': product.id,
            'qty': 1,
            'price_unit': 100.0,
            'price_subtotal': 100.0,
            'price_subtotal_incl': 100.0,
            'tax_ids': [],
            **values,
        })

    def _sync(self, lines, mapping=None, **order_values):
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
            **order_values,
        }
        if mapping:
            order['relations_uuid_mapping'] = {'pos.order.line': mapping}
        result = self.env['pos.order'].sync_from_ui([order])
        return self.env['pos.order'].browse(result['pos.order'][0]['id'])

    def _tinted(self, base_values=None, colorant_values=None):
        """Base entintada con un colorante hijo, como la envía la caja."""
        base_uuid, colorant_uuid = str(uuid4()), str(uuid4())
        lines = [
            self._line(self.base, base_uuid, **(base_values or {})),
            self._line(self.colorant, colorant_uuid, qty=24, price_unit=0.0,
                       price_subtotal=0.0, price_subtotal_incl=0.0, **(colorant_values or {})),
        ]
        return lines, {colorant_uuid: {'combo_parent_id': base_uuid}}

    def _create_order(self, line_values, **order_values):
        return self.env['pos.order'].create({
            'session_id': self.session.id,
            'partner_id': self.partner.id,
            'amount_total': 100.0,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'lines': [Command.create({
                'product_id': self.product.id,
                'qty': 1,
                'price_unit': 100.0,
                'price_subtotal': 100.0,
                'price_subtotal_incl': 100.0,
                **line_values,
            })],
            **order_values,
        })

    # --- Persistencia ---------------------------------------------------

    def test_line_promotion_is_persisted(self):
        order = self._sync(
            [self._line(self.product, discount=20.0, pricing_rule_type='promo',
                        promo_reward_id=self.order_reward.id)],
            order_promo_reward_id=self.order_reward.id,
        )
        self.assertEqual(order.order_promo_reward_id, self.order_reward)
        self.assertEqual(order.lines.promo_reward_id, self.order_reward)
        self.assertEqual(order.lines.pricing_rule_type, 'promo')

    def test_pos_promotion_counts_usage(self):
        initial = self.program.order_count
        self._create_order(
            {'discount': 15.0, 'pricing_rule_type': 'promo', 'promo_reward_id': self.line_reward.id},
            state='paid', amount_paid=85.0,
        )
        self.program.invalidate_recordset(['order_count'])
        self.assertEqual(self.program.order_count, initial + 1)

    def test_manual_pricing_rule_type_accepted(self):
        order = self._sync([self._line(self.product, discount=5.0, pricing_rule_type='manual',
                                       pricing_rule_origin='Manual · 5%')])
        self.assertEqual(order.lines.pricing_rule_type, 'manual')

    def test_selection_values_match_sales(self):
        pos_fields = self.env['pos.order.line']._fields
        sale_fields = self.env['sale.order.line']._fields
        for name in ('price_origin', 'pricing_rule_type'):
            pos_values = [value for value, _label in pos_fields[name].selection]
            sale_values = [value for value, _label in sale_fields[name].selection]
            self.assertEqual(pos_values, sale_values, name)
        origins = [value for value, _label in pos_fields['price_origin'].selection]
        self.assertNotIn('agreement', origins)
        self.assertNotIn('promo', origins)
        self.assertIn('manual', [value for value, _label in pos_fields['pricing_rule_type'].selection])

    # --- Coherencia (C1–C6) ---------------------------------------------

    def test_rejects_fixed_price_with_discount(self):
        with self.assertRaisesRegex(UserError, 'tiene precio fijo de acuerdo: no admite descuento'):
            self._sync([self._line(self.product, discount=10.0, pricing_rule_type='fixed_price')])

    def test_rejects_non_line_promotion_reward(self):
        with self.assertRaisesRegex(UserError, 'no es una promoción por línea'):
            self._sync([self._line(self.product, pricing_rule_type='promo',
                                   promo_reward_id=self.amount_reward.id)])

    def test_rejects_promotion_on_ineligible_line(self):
        # Producto de un combo: la promoción va en su paquete.
        package_uuid, item_uuid = str(uuid4()), str(uuid4())
        with self.assertRaisesRegex(UserError, 'no admite promociones'):
            self._sync(
                [self._line(self.other_product, package_uuid),
                 self._line(self.product, item_uuid, discount=15.0, pricing_rule_type='promo',
                            promo_reward_id=self.line_reward.id)],
                {item_uuid: {'combo_parent_id': package_uuid}},
            )
        # Colorante: lo detiene primero la regla propia del colorante.
        lines, mapping = self._tinted(colorant_values={
            'pricing_rule_type': 'promo', 'promo_reward_id': self.line_reward.id,
        })
        with self.assertRaisesRegex(UserError, 'no lleva acuerdo ni promoción'):
            self._sync(lines, mapping)
        # Anticipo: se revisa sobre el pedido, sin las reglas de cobro de anticipos.
        order = self._create_order({
            'pricing_rule_type': 'promo',
            'promo_reward_id': self.line_reward.id,
            'advance_source_ref': 1,
        })
        self.assertTrue(order.lines._is_accounting_advance_line())
        with self.assertRaisesRegex(UserError, 'no admite promociones'):
            order._check_pricing_coherence()

    def test_rejects_promo_type_without_reward(self):
        with self.assertRaisesRegex(UserError, 'indica una promoción que no trae'):
            self._sync([self._line(self.product, discount=10.0, pricing_rule_type='promo')])

    def test_rejects_agreement_on_colorant(self):
        lines, mapping = self._tinted(colorant_values={'pricing_rule_type': 'product', 'discount': 10.0})
        with self.assertRaisesRegex(UserError, 'no lleva acuerdo ni promoción'):
            self._sync(lines, mapping)

    def test_rejects_non_order_reward_on_order(self):
        with self.assertRaisesRegex(UserError, 'no es una promoción de pedido'):
            self._sync([self._line(self.product)], order_promo_reward_id=self.line_reward.id)

    def test_tinted_base_with_discount_accepted(self):
        lines, mapping = self._tinted(
            base_values={'discount': 10.0, 'pricing_rule_type': 'product',
                         'pricing_rule_origin': 'Producto · 10%'},
            colorant_values={'discount': 10.0, 'pricing_rule_type': 'none'},
        )
        order = self._sync(lines, mapping)
        colorant_line = order.lines.filtered(lambda line: line.product_id == self.colorant)
        self.assertEqual(colorant_line.combo_parent_id.product_id, self.base)
        self.assertEqual(colorant_line.discount, 10.0)

    # --- Carga a la caja ------------------------------------------------

    def test_discount_rules_loaded_only_for_loaded_partners(self):
        Rule = self.env['res.partner.discount.rule']
        partners = self.partner | self._create_customer('Cliente PDV precios 2')
        unloaded = self._create_customer('Cliente PDV precios sin cargar')
        rules = Rule.create([
            {'partner_id': partner.id, 'applied_on': '0_product', 'product_id': self.product.id,
             'rule_type': 'discount', 'discount': 10.0}
            for partner in partners | unloaded
        ])
        data = {'res.partner': [{'id': partner.id} for partner in partners]}
        loaded = Rule.search(Rule._load_pos_data_domain(data, self.config))
        self.assertEqual(loaded & rules, rules.filtered(lambda rule: rule.partner_id in partners))

        # Un cliente que la caja busca después llega con sus reglas.
        response = self.env['res.partner'].get_new_partner(self.config.id, [('id', '=', unloaded.id)], 0)
        rule_ids = {rule['id'] for rule in response['res.partner.discount.rule']}
        self.assertEqual(rule_ids, set(rules.filtered(lambda rule: rule.partner_id == unloaded).ids))

    # --- Recompensas y cotización ---------------------------------------

    def test_reward_used_in_pos_is_archived(self):
        order = self._create_order({
            'discount': 15.0, 'pricing_rule_type': 'promo', 'promo_reward_id': self.line_reward.id,
        })
        self.line_reward.unlink()
        self.assertTrue(self.line_reward.exists())
        self.assertFalse(self.line_reward.active)
        self.assertEqual(order.lines.promo_reward_id, self.line_reward)

    def test_quotation_from_ticket_keeps_promotion(self):
        ticket = self._create_order(
            {'discount': 20.0, 'price_subtotal': 80.0, 'price_subtotal_incl': 80.0,
             'pricing_rule_type': 'promo', 'promo_reward_id': self.order_reward.id,
             'tax_ids': [Command.clear()]},
            pricelist_id=self.pricelist.id, amount_total=80.0, state='draft',
        )
        result = ticket.action_create_sale_quotation()
        quotation = self.env['sale.order'].browse(result['id'])
        self.assertEqual(quotation.order_line.promo_reward_id, self.order_reward)
        self.assertEqual(quotation.order_line.pricing_rule_type, 'promo')
        self.assertEqual(quotation.order_line.price_origin, ticket.lines.price_origin)
