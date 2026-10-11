# -*- coding: utf-8 -*-

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestResPartnerPhone(TransactionCase):
    """ Casos de prueba del teléfono en res.partner:

    - Solo los canales del usuario (web_save, web_save_multi, load) exigen
      el teléfono; lo que se crea por código puede quedar sin él.

    - Contactos mexicanos (country_id = MX, o sin país) deben tener
      exactamente 10 dígitos.
    - Contactos extranjeros se validan con la librería `phonenumbers`
      (módulo core `phone_validation`) según su país real, en vez de la
      regla fija de 10 dígitos.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.country_mx = cls.env.ref('base.mx')
        cls.country_us = cls.env.ref('base.us')

    def _create_partner(self, **values):
        values.setdefault('name', 'Contacto de prueba')
        return self.env['res.partner'].create(values)

    # -- Creación por código: el teléfono no es obligatorio ---------------

    def test_orm_create_without_phone_is_allowed(self):
        partner = self._create_partner(phone=False)
        self.assertFalse(partner.phone)

    def test_orm_create_with_blank_phone_is_allowed(self):
        partner = self._create_partner(phone='   ')
        self.assertTrue(partner)

    def test_orm_create_still_validates_format(self):
        with self.assertRaises(ValidationError):
            self._create_partner(phone='551234567', country_id=self.country_mx.id)

    def test_name_create_without_phone_is_allowed(self):
        partner_id, _name = self.env['res.partner'].name_create('Rápido Contacto')
        self.assertFalse(self.env['res.partner'].browse(partner_id).phone)

    def test_users_and_companies_without_phone_are_allowed(self):
        user = self.env['res.users'].create({
            'name': 'Usuario Sin Teléfono',
            'login': 'usuario_sin_telefono',
        })
        self.assertFalse(user.partner_id.phone)
        company = self.env['res.company'].create({'name': 'Compañía Sin Teléfono'})
        self.assertFalse(company.partner_id.phone)

    # -- Formularios (web_save / web_save_multi) -------------------------

    def test_web_save_new_partner_without_phone_fails(self):
        Partner = self.env['res.partner']
        with self.assertRaises(ValidationError):
            Partner.web_save({'name': 'Sin Teléfono Form'}, {})
        self.assertFalse(Partner.search([('name', '=', 'Sin Teléfono Form')]))

    def test_web_save_new_partner_with_blank_phone_fails(self):
        Partner = self.env['res.partner']
        with self.assertRaises(ValidationError):
            Partner.web_save({'name': 'Teléfono Blanco', 'phone': '   '}, {})
        self.assertFalse(Partner.search([('name', '=', 'Teléfono Blanco')]))

    def test_web_save_new_partner_with_phone_saves(self):
        result = self.env['res.partner'].web_save(
            {'name': 'Con Teléfono Form', 'phone': '5512345678'}, {})
        partner = self.env['res.partner'].browse(result[0]['id'])
        self.assertEqual(partner.phone, '5512345678')

    def test_web_save_clearing_existing_phone_fails(self):
        partner = self._create_partner(phone='5512345678')
        with self.assertRaises(ValidationError):
            partner.web_save({'phone': False}, {})
        self.assertEqual(partner.phone, '5512345678')

    def test_web_save_existing_partner_without_phone_fails_on_any_change(self):
        partner = self._create_partner(phone=False)
        with self.assertRaises(ValidationError):
            partner.web_save({'name': 'Nombre Nuevo'}, {})

    def test_web_save_new_partner_with_child_without_phone_fails(self):
        Partner = self.env['res.partner']
        with self.assertRaises(ValidationError):
            Partner.web_save({
                'name': 'Padre Form',
                'phone': '5512345678',
                'child_ids': [Command.create({'name': 'Hijo Sin Teléfono'})],
            }, {})
        self.assertFalse(Partner.search([('name', 'in', ['Padre Form', 'Hijo Sin Teléfono'])]))

    def test_web_save_new_partner_with_child_with_phone_saves(self):
        result = self.env['res.partner'].web_save({
            'name': 'Padre Con Hijo',
            'phone': '5512345678',
            'child_ids': [Command.create({'name': 'Hijo Con Teléfono', 'phone': '5587654321'})],
        }, {})
        parent = self.env['res.partner'].browse(result[0]['id'])
        self.assertEqual(parent.child_ids.phone, '5587654321')

    def test_web_save_adding_child_without_phone_fails(self):
        parent = self._create_partner(phone='5512345678')
        with self.assertRaises(ValidationError):
            parent.web_save({
                'child_ids': [Command.create({'name': 'Hijo Nuevo Sin Teléfono'})],
            }, {})
        self.assertFalse(parent.child_ids)

    def test_web_save_updating_child_clears_phone_fails(self):
        parent = self._create_partner(phone='5512345678')
        child = self._create_partner(
            name='Hijo Editado', phone='5587654321', parent_id=parent.id)
        with self.assertRaises(ValidationError):
            parent.web_save({
                'child_ids': [Command.update(child.id, {'phone': False})],
            }, {})
        self.assertEqual(child.phone, '5587654321')

    def test_web_save_parent_with_untouched_old_child_without_phone_saves(self):
        parent = self._create_partner(phone='5512345678')
        self._create_partner(name='Hijo Antiguo', phone=False, parent_id=parent.id)
        parent.web_save({'name': 'Padre Renombrado'}, {})
        self.assertEqual(parent.name, 'Padre Renombrado')

    def test_web_save_multi_without_phone_fails(self):
        partners = self._create_partner(name='Lista A', phone='5512345678') \
            | self._create_partner(name='Lista B', phone='5587654321')
        with self.assertRaises(ValidationError):
            partners.web_save_multi([{'phone': False}, {'name': 'Lista B2'}], {})
        self.assertEqual(partners.mapped('phone'), ['5512345678', '5587654321'])

    # -- Importación (load) ------------------------------------------------

    def test_load_with_missing_phone_imports_nothing(self):
        Partner = self.env['res.partner']
        result = Partner.load(
            ['name', 'phone'],
            [['Import Uno', '5512345678'], ['Import Dos', '']],
        )
        self.assertFalse(result['ids'])
        errors = [m for m in result['messages'] if m['type'] == 'error']
        self.assertTrue(errors)
        self.assertIn('Import Dos', errors[0]['message'])
        self.assertFalse(Partner.search([('name', 'in', ['Import Uno', 'Import Dos'])]))

    def test_load_with_phones_imports_partners(self):
        result = self.env['res.partner'].load(
            ['name', 'phone'],
            [['Import Tres', '5512345678'], ['Import Cuatro', '5587654321']],
        )
        self.assertEqual(len(result['ids']), 2)

    # -- México: país MX o sin país registrado ---------------------------

    def test_mx_phone_10_digits_ok(self):
        partner = self._create_partner(
            phone='5512345678', country_id=self.country_mx.id)
        self.assertTrue(partner)

    def test_mx_phone_with_prefix_and_spaces_ok(self):
        # El +52 y los espacios que agrega el widget deben aceptarse.
        partner = self._create_partner(
            phone='+52 55 1234 5678', country_id=self.country_mx.id)
        self.assertTrue(partner)

    def test_mx_phone_without_country_defaults_to_mx_ok(self):
        partner = self._create_partner(phone='5512345678', country_id=False)
        self.assertTrue(partner)

    def test_mx_phone_without_country_wrong_length_fails(self):
        with self.assertRaises(ValidationError):
            self._create_partner(phone='551234567', country_id=False)  # 9 dígitos

    def test_mx_phone_too_short_fails(self):
        with self.assertRaises(ValidationError):
            self._create_partner(
                phone='551234567', country_id=self.country_mx.id)  # 9 dígitos

    def test_mx_phone_too_long_fails(self):
        with self.assertRaises(ValidationError):
            self._create_partner(
                phone='55123456789', country_id=self.country_mx.id)  # 11 dígitos

    def test_mx_phone_with_letters_fails(self):
        with self.assertRaises(ValidationError):
            self._create_partner(
                phone='55ABCD5678', country_id=self.country_mx.id)

    # -- Extranjero: delega en phone_validation/phonenumbers -------------

    def test_foreign_phone_valid_us_number_ok(self):
        # Número de ejemplo estándar de Google/libphonenumber, siempre
        # válido para pruebas (+1 650-253-0000).
        partner = self._create_partner(
            phone='+1 650-253-0000', country_id=self.country_us.id)
        self.assertTrue(partner)

    def test_foreign_phone_too_short_fails(self):
        with self.assertRaises(ValidationError):
            self._create_partner(phone='+1 123', country_id=self.country_us.id)

    def test_foreign_phone_invalid_area_code_fails(self):
        # En EE.UU. (NANP) ninguna lada empieza en "0" o "1".
        with self.assertRaises(ValidationError):
            self._create_partner(
                phone='0123456789', country_id=self.country_us.id)

    # -- Cambiar el país debe volver a validar el teléfono ----------------

    def test_changing_country_revalidates_phone(self):
        partner = self._create_partner(
            phone='0123456789', country_id=self.country_mx.id)
        with self.assertRaises(ValidationError):
            partner.country_id = self.country_us.id
