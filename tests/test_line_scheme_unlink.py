# -*- coding: utf-8 -*-

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged


class LineSchemeUnlinkCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schemes = cls.env['tint.schema']
        cls.lines = cls.env['lines.product']
        cls.rules = cls.env['res.partner.discount.rule']
        cls.white = cls.env['tint.base.type'].search([('code', '=', 'W')], limit=1)
        cls.gallon = cls.env['tint.size'].search([('code', '=', 'G')], limit=1)
        cls.scheme = cls.schemes.create({'name': 'Esquema Borrado'})
        cls.line = cls.lines.create({'name': 'Línea Borrado', 'scheme': cls.scheme.id})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Borrado', 'is_customer': True, 'phone': '5512345678',
        })
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'Tarifa Borrado'})
        cls.gallery = cls.env['tint.gallery'].create({'name': 'Galería Borrado', 'code': 'S31-GAL'})

    def _line_rule(self, line, partner=None):
        return self.rules.create({
            'partner_id': (partner or self.partner).id,
            'applied_on': '1_line',
            'rule_type': 'discount',
            'line_id': line.id,
            'discount': 10.0,
        })

    def _scheme_rule(self, scheme, partner=None):
        return self.rules.create({
            'partner_id': (partner or self.partner).id,
            'applied_on': '2_scheme',
            'rule_type': 'discount',
            'scheme_id': scheme.id,
            'discount': 10.0,
        })

    def _pricelist_item(self, scheme, line=None):
        return self.env['product.pricelist.item'].create({
            'pricelist_id': self.pricelist.id,
            'display_applied_on': '3_esquema',
            'esquema_id': scheme.id,
            'linea_id': line.id if line else False,
            'compute_price': 'percentage',
            'percent_price': 20.0,
        })

    def _base(self, line, code, active=True):
        return self.env['product.template'].create({
            'name': f'Base {code}',
            'default_code': code,
            'is_storable': True,
            'tint_role': 'base',
            'tint_base_type_id': self.white.id,
            'tint_size_id': self.gallon.id,
            'lines_product_id': line.id,
            'active': active,
        })

    def _formula(self, line, code, active=True):
        color = self.env['tint.color'].create({
            'name': f'Color {code}', 'code': code, 'gallery_id': self.gallery.id,
        })
        return self.env['tint.color.formula'].create({
            'color_id': color.id,
            'base_type_id': self.white.id,
            'size_id': self.gallon.id,
            'line_scheme_id': line.id,
            'active': active,
        })


@tagged('post_install', '-at_install')
class TestLineUnlink(LineSchemeUnlinkCommon):
    """ Una línea de producto en uso no se borra: el mensaje cuenta lo que la usa. """

    def test_unused_line_is_deleted(self):
        presentation = self.env['lines.product.presentation'].create({
            'line_id': self.line.id, 'presentation_id': self.gallon.id,
        })
        self.line.unlink()
        self.assertFalse(self.line.exists())
        self.assertFalse(presentation.exists())

    def test_line_with_agreement_blocked(self):
        rule = self._line_rule(self.line)
        with self.assertRaises(UserError) as error:
            self.line.unlink()
        self.assertIn('1 acuerdo de cliente', str(error.exception))
        self.assertTrue(self.line.exists())
        self.assertTrue(rule.exists())
        self.assertEqual(rule.line_id, self.line)

    def test_line_with_products_blocked(self):
        active = self._base(self.line, 'S31-B1')
        archived = self._base(self.line, 'S31-B2', active=False)
        with self.assertRaises(UserError) as error:
            self.line.unlink()
        self.assertIn('2 productos', str(error.exception))
        for product in active | archived:
            self.assertEqual(product.lines_product_id, self.line)
            self.assertEqual(product.scheme_id, self.scheme)

    def test_line_with_pricelist_item_blocked(self):
        item = self._pricelist_item(self.scheme, self.line)
        with self.assertRaises(UserError) as error:
            self.line.unlink()
        self.assertIn('1 regla de tarifa', str(error.exception))
        self.assertEqual(item.linea_id, self.line)

    def test_line_with_archived_formula_blocked(self):
        formula = self._formula(self.line, 'S31-F1', active=False)
        with self.assertRaises(UserError) as error:
            self.line.unlink()
        self.assertIn('1 fórmula', str(error.exception))
        self.assertEqual(formula.line_scheme_id, self.line)

    def test_line_message_counts_all_types(self):
        self._base(self.line, 'S31-B3')
        self._base(self.line, 'S31-B4')
        self._base(self.line, 'S31-B5')
        self._line_rule(self.line)
        self._line_rule(self.line, self.env['res.partner'].create({
            'name': 'Otro cliente Borrado', 'is_customer': True, 'phone': '5512345679',
        }))
        self._pricelist_item(self.scheme, self.line)
        for code in ('S31-F2', 'S31-F3', 'S31-F4', 'S31-F5'):
            self._formula(self.line, code)
        with self.assertRaises(UserError) as error:
            self.line.unlink()
        message = str(error.exception)
        self.assertIn('Línea Borrado', message)
        self.assertIn('3 productos, 2 acuerdos de cliente, 1 regla de tarifa y 4 fórmulas', message)
        self.assertIn('Archívala en su lugar', message)

    def test_line_other_company_agreement_blocked(self):
        other_company = self.env['res.company'].create({'name': 'Compañía Borrado 2', 'phone': '1234567890'})
        other_partner = self.env['res.partner'].create({
            'name': 'Cliente otra compañía', 'is_customer': True, 'phone': '5512345670',
            'company_id': other_company.id,
        })
        rule = self._line_rule(self.line, other_partner)
        manager = new_test_user(
            self.env, login='spec31_tint_manager',
            groups='base.group_user,entintados_pdv.group_tint_manager',
            company_id=self.env.company.id, company_ids=[self.env.company.id],
        )
        with self.assertRaises(UserError) as error:
            self.line.with_user(manager).unlink()
        self.assertIn('1 acuerdo de cliente', str(error.exception))
        self.assertTrue(self.line.exists())
        self.assertTrue(rule.exists())

    def test_line_multi_unlink_all_or_nothing(self):
        free = self.lines.create({'name': 'Línea Libre', 'scheme': self.scheme.id})
        self._line_rule(self.line)
        with self.assertRaises(UserError) as error:
            (free | self.line).unlink()
        message = str(error.exception)
        self.assertIn('Línea Borrado', message)
        self.assertNotIn('Línea Libre', message)
        self.assertTrue(free.exists())
        self.assertTrue(self.line.exists())

    def test_line_agreement_db_restrict(self):
        self.assertEqual(self.rules._fields['line_id'].ondelete, 'restrict')
        self.assertEqual(self.env['product.pricelist.item']._fields['linea_id'].ondelete, 'restrict')


@tagged('post_install', '-at_install')
class TestSchemeUnlink(LineSchemeUnlinkCommon):
    """ Un esquema en uso no se borra: el mensaje cuenta lo que lo usa. """

    def test_unused_scheme_is_deleted(self):
        scheme = self.schemes.create({'name': 'Esquema Libre'})
        scheme.unlink()
        self.assertFalse(scheme.exists())

    def test_scheme_with_agreement_blocked(self):
        scheme = self.schemes.create({'name': 'Esquema Con Acuerdo'})
        rule = self._scheme_rule(scheme)
        with self.assertRaises(UserError) as error:
            scheme.unlink()
        message = str(error.exception)
        self.assertIn('1 acuerdo de cliente', message)
        self.assertIn('Archívalo en su lugar', message)
        self.assertTrue(scheme.exists())
        self.assertTrue(rule.exists())

    def test_scheme_with_pricelist_item_blocked(self):
        scheme = self.schemes.create({'name': 'Esquema Con Tarifa'})
        item = self._pricelist_item(scheme)
        with self.assertRaises(UserError) as error:
            scheme.unlink()
        self.assertIn('1 regla de tarifa', str(error.exception))
        self.assertEqual(item.esquema_id, scheme)

    def test_scheme_with_lines_blocked(self):
        self.lines.create({'name': 'Segunda Línea Borrado', 'scheme': self.scheme.id})
        with self.assertRaises(UserError) as error:
            self.scheme.unlink()
        self.assertIn('2 líneas de producto', str(error.exception))
        self.assertTrue(self.scheme.exists())

    def test_scheme_agreement_db_restrict(self):
        self.assertEqual(self.rules._fields['scheme_id'].ondelete, 'restrict')
        self.assertEqual(self.env['product.pricelist.item']._fields['esquema_id'].ondelete, 'restrict')
