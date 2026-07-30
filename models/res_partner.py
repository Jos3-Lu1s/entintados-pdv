# -*- coding: utf-8 -*-

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

PHONE_REGEX = re.compile(r'^\d{10}$')

MX_COUNTRY_CODE = '52'
MX_ISO_CODE = 'MX'

# RFC: 3-4 letras + fecha (AAMMDD) + 3 caracteres de homoclave.
# La fecha se valida con mes y día en rangos válidos.
RFC_REGEX = re.compile(
    r'^[A-ZÑ&]{3,4}'
    r'\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])'
    r'[A-Z0-9]{3}$'
)

GENERIC_RFCS = {
    'XAXX010101000',  # Público en general
    'XEXX010101000',  # Extranjeros
}


class ResPartner(models.Model):
    _inherit = 'res.partner'

    phone = fields.Char(required=True)

    is_customer = fields.Boolean(
        string="Cliente",
        help="Marca este contacto como cliente de la empresa.",
    )
    is_creditor = fields.Boolean(
        string="Acreedor",
        help="Marca este contacto como acreedor.",
    )
    is_supplier = fields.Boolean(
        string="Proveedor",
        help="Marca este contacto como proveedor de bienes o servicios.",
    )
    is_distributor = fields.Boolean(
        string="Distribuidor",
        help="Marca este contacto como distribuidor autorizado.",
    )

    # Centralizan la lógica para identificar contactos de ventas y compras,
    # evitando repetir la misma condición en dominios y filtros.
    is_sales_contact = fields.Boolean(
        string="Contacto de ventas",
        compute='_compute_is_sales_contact',
        store=True,
        help="Cliente o distribuidor. Se usa para restringir el selector de "
             "contactos en el POS y en el campo Cliente de Ventas.",
    )
    is_purchase_contact = fields.Boolean(
        string="Contacto de compras",
        compute='_compute_is_purchase_contact',
        store=True,
        help="Proveedor o acreedor. Se usa para restringir el campo "
             "Proveedor en Compras.",
    )
    
    is_told=fields.Boolean(
        string="Contado",
        default=True,
    )
    is_credit=fields.Boolean(
        string="Crédito",
        help="Marca este contacto como cliente de crédito.",
    )

    @api.depends('is_customer', 'is_distributor')
    def _compute_is_sales_contact(self):
        for partner in self:
            partner.is_sales_contact = partner.is_customer or partner.is_distributor

    @api.depends('is_supplier', 'is_creditor')
    def _compute_is_purchase_contact(self):
        for partner in self:
            partner.is_purchase_contact = partner.is_supplier or partner.is_creditor

    def _raise_vat_duplicate_error(self, vat, duplicate):
        raise ValidationError(_(
            'Ya existe un contacto activo con el mismo RFC/VAT (%(vat)s): '
            '"%(name)s" (ID %(id)s).\n'
            'Verifica si es el mismo cliente o proveedor antes de crear uno '
            'nuevo. Si de verdad son duplicados, usa la herramienta de '
            'fusión de contactos (Contactos > seleccionar los registros > '
            'Acción > Fusionar) en lugar de conservar los dos.',
            vat=vat, name=duplicate.display_name, id=duplicate.id,
        ))

    @api.model_create_multi
    def create(self, vals_list):
        payment_term = self.env.ref(
            'account.account_payment_term_immediate',
            raise_if_not_found=False,
        )

        for vals in vals_list:
            # Normalizar y validar RFC/VAT duplicado
            if isinstance(vals.get('vat'), str):
                vat = vals['vat'].strip().upper()
                vals['vat'] = vat

                if vat:
                    duplicate = self._find_duplicate_by_vat(vat)

                    if duplicate:
                        self._raise_vat_duplicate_error(vat, duplicate)

            # Asignar plazo de pago de 30 días por defecto
            if not vals.get('property_payment_term_id') and payment_term:
                vals['property_payment_term_id'] = payment_term.id

        return super().create(vals_list)

    def write(self, vals):
        if 'vat' in vals and isinstance(vals['vat'], str):
            vat = vals['vat'].strip().upper()
            vals['vat'] = vat
            for partner in self:
                duplicate = self._find_duplicate_by_vat(vat, exclude_ids=partner.ids)
                if duplicate:
                    self._raise_vat_duplicate_error(vat, duplicate)
        return super().write(vals)

    @api.onchange('vat')
    def _onchange_vat_uppercase(self):
        if self.vat and isinstance(self.vat, str):
            self.vat = self.vat.strip().upper()

    def init(self):
        # Normaliza RFC existentes en la base de datos a mayúsculas.
        self.env.cr.execute("""
            UPDATE res_partner
            SET vat = UPPER(TRIM(vat))
            WHERE vat IS NOT NULL AND vat != UPPER(TRIM(vat));
        """)
        # Índice único para RFC activos; ignora archivados y RFC genéricos.
        # Se recrea para mantener la definición actual.
        super().init()
        self.env.cr.execute("DROP INDEX IF EXISTS res_partner_vat_uniq_active_idx")
        self.env.cr.execute("""
            CREATE UNIQUE INDEX res_partner_vat_uniq_active_idx
            ON res_partner (upper(trim(vat)))
            WHERE vat IS NOT NULL AND trim(vat) != '' AND active = true
                  AND upper(trim(vat)) NOT IN %(generic_rfcs)s
        """, {'generic_rfcs': tuple(GENERIC_RFCS)})

    def _find_duplicate_by_vat(self, vat, exclude_ids=None):
        vat = (vat or '').strip()
        if not vat or vat.upper() in GENERIC_RFCS:
            return self.browse()
        domain = [('vat', '=ilike', vat), ('active', '=', True)]
        if exclude_ids:
            domain.append(('id', 'not in', exclude_ids))
        return self.sudo().search(domain, limit=1)

    @api.constrains('vat')
    def _check_vat_duplicate(self):
        for partner in self:
            duplicate = self._find_duplicate_by_vat(partner.vat, exclude_ids=partner.ids)
            if duplicate:
                self._raise_vat_duplicate_error(partner.vat, duplicate)

    @api.constrains('vat')
    def _check_vat_format(self):
        for partner in self:
            vat = (partner.vat or '').strip().upper()
            if not vat or vat in GENERIC_RFCS:
                continue
            if not RFC_REGEX.match(vat):
                raise ValidationError(_(
                    'El RFC "%(vat)s" de "%(name)s" no tiene una estructura '
                    'válida. Debe ser persona física (4 letras + fecha AAMMDD '
                    '+ 3 caracteres de homoclave, 13 en total, ej. '
                    'XEXX010101000) o persona moral (3 letras + fecha AAMMDD '
                    '+ homoclave, 12 en total). Para público en general o '
                    'extranjeros sin RFC usa los genéricos: %(generic)s.',
                    vat=partner.vat, name=partner.display_name,
                    generic=' / '.join(sorted(GENERIC_RFCS)),
                ))

    def _phone_digits(self, phone):
        # Deja solo dígitos y, si el widget nativo antepuso el código de
        # país (+52), lo quita para comparar contra el número real de 10.
        digits = re.sub(r'\D', '', phone or '')
        if len(digits) == 12 and digits.startswith(MX_COUNTRY_CODE):
            digits = digits[len(MX_COUNTRY_CODE):]
        return digits

    def _is_mexican_partner(self, partner):
        # Sin país registrado se considera un contacto de México.
        return not partner.country_id or partner.country_id.code == MX_ISO_CODE

    @api.constrains('phone', 'country_id')
    def _check_phone_format(self):
        for partner in self:
            if not partner.phone:
                raise ValidationError(_(
                    'El teléfono es obligatorio. Falta en: %(name)s.',
                    name=partner.display_name,
                ))
            if self._is_mexican_partner(partner):
                if not PHONE_REGEX.match(self._phone_digits(partner.phone)):
                    raise ValidationError(_(
                        'El teléfono de "%(name)s" debe tener 10 dígitos '
                        '(puede incluir el +52 y espacios que agrega el '
                        'campo, pero al quitarlos deben quedar exactamente '
                        '10). Valor actual: %(phone)s.',
                        name=partner.display_name, phone=partner.phone,
                    ))
            else:
                # Para contactos extranjeros se reutiliza la validación estándar
                # de Odoo, que verifica el formato según el país (country_id).
                try:
                    partner._phone_format(
                        fname='phone',
                        country=partner.country_id,
                        raise_exception=True,
                    )
                except UserError as error:
                    raise ValidationError(_(
                        'El teléfono de "%(name)s" no es válido para '
                        '%(country)s: %(error)s',
                        name=partner.display_name,
                        country=partner.country_id.name,
                        error=str(error),
                    )) from error

    @api.model
    def _load_pos_data_domain(self, data, config):
        # EXTENDS point_of_sale
        # Además de los partners ya requeridos por el core (los de órdenes
        # cargadas y el propio cajero), solo precargar contactos de ventas.
        domain = super()._load_pos_data_domain(data, config)
        return domain + [('is_sales_contact', '=', True)]

    @api.model
    def get_new_partner(self, config_id, domain, offset):
        # OVERRIDES point_of_sale (no se puede usar super() a medias: la
        # restricción is_sales_contact debe aplicarse en las dos ramas de
        # búsqueda del método original). Revisar este override si el core
        # cambia la lógica de `get_new_partner` en una futura versión.
        config = self.env['pos.config'].browse(config_id)
        sales_domain = [('is_sales_contact', '=', True)]
        if len(domain) == 0:
            limited_partner_ids = {
                partner[0] for partner in config.get_limited_partners_loading(offset)
            }
            new_partners = self.search([('id', 'in', list(limited_partner_ids))] + sales_domain)
        else:
            # If search domain is not empty, we need to search inside all partners
            new_partners = self.search(domain + sales_domain, offset=offset, limit=100)
        fiscal_positions = new_partners.fiscal_position_id
        return {
            'res.partner': self._load_pos_data_read(new_partners, config),
            'account.fiscal.position': self.env['account.fiscal.position']._load_pos_data_read(fiscal_positions, config),
        }
