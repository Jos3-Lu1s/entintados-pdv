# -*- coding: utf-8 -*-

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestResPartnerPhone(TransactionCase):
    """ Casos de prueba de `_check_phone_format` en res.partner:

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

    # -- Teléfono obligatorio -------------------------------------------

    def test_phone_required(self):
        with self.assertRaises(ValidationError):
            self._create_partner(phone=False)

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
