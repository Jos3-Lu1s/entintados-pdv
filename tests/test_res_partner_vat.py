# -*- coding: utf-8 -*-

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestResPartnerVat(TransactionCase):
    """ Casos de prueba de `vat` (RFC) en res.partner:

    - Se convierte automáticamente a mayúsculas al crear o modificar.
    - Valida formato RFC de personas físicas (13 caracteres) y morales (12 caracteres).
    - Detecta duplicados entre contactos activos.
    - Permite RFCs genéricos (XAXX010101000, XEXX010101000) repetidos.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['res.partner']

    def _create_partner(self, **values):
        values.setdefault('name', 'Contacto RFC Prueba')
        values.setdefault('phone', '5512345678')
        return self.partner_model.create(values)

    def test_vat_converted_to_uppercase_on_create(self):
        partner = self._create_partner(vat='  gacr900101h88  ')
        self.assertEqual(partner.vat, 'GACR900101H88')

    def test_vat_converted_to_uppercase_on_write(self):
        partner = self._create_partner(vat='GACR900101H88')
        partner.write({'vat': 'xaXX010101000'})
        self.assertEqual(partner.vat, 'XAXX010101000')

    def test_vat_moral_person_12_chars_ok(self):
        partner = self._create_partner(vat='abc120304xxx')
        self.assertEqual(partner.vat, 'ABC120304XXX')

    def test_vat_invalid_format_fails(self):
        with self.assertRaises(ValidationError):
            self._create_partner(vat='INVALID_RFC_123')

    def test_vat_duplicate_fails(self):
        self._create_partner(vat='GACR900101H88')
        with self.assertRaises(ValidationError):
            self._create_partner(vat='gacr900101h88')

    def test_vat_generic_rfc_duplicates_allowed(self):
        p1 = self._create_partner(name='Cliente 1', vat='xaxx010101000')
        p2 = self._create_partner(name='Cliente 2', vat='XAXX010101000')
        self.assertEqual(p1.vat, 'XAXX010101000')
        self.assertEqual(p2.vat, 'XAXX010101000')
