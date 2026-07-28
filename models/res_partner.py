# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = 'res.partner'

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

    # Campos computados que centralizan la regla de negocio "quién cuenta
    # como contacto de ventas / de compras". Todo el módulo (POS, vistas de
    # Ventas y Compras, acciones y filtros de Contactos) debe filtrar usando
    # estos dos campos en vez de repetir la combinación OR en cada lugar.
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

    @api.depends('is_customer', 'is_distributor')
    def _compute_is_sales_contact(self):
        for partner in self:
            partner.is_sales_contact = partner.is_customer or partner.is_distributor

    @api.depends('is_supplier', 'is_creditor')
    def _compute_is_purchase_contact(self):
        for partner in self:
            partner.is_purchase_contact = partner.is_supplier or partner.is_creditor

    def init(self):
        # Evita RFC/VAT duplicados en contactos activos mediante un índice único
        # en la base de datos. Normaliza el RFC (sin espacios y en mayúsculas) e
        # ignora registros sin RFC o archivados.
        super().init()
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS res_partner_vat_uniq_active_idx
            ON res_partner (upper(trim(vat)))
            WHERE vat IS NOT NULL AND trim(vat) != '' AND active = true
        """)

    def _find_duplicate_by_vat(self, vat, exclude_ids=None):
        vat = (vat or '').strip()
        if not vat:
            return self.browse()
        domain = [('vat', '=ilike', vat), ('active', '=', True)]
        if exclude_ids:
            domain.append(('id', 'not in', exclude_ids))
        return self.sudo().search(domain, limit=1)

    def _check_duplicate_vat(self, vat, exclude_ids=None):
        duplicate = self._find_duplicate_by_vat(vat, exclude_ids=exclude_ids)
        if duplicate:
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
        for vals in vals_list:
            vat = vals.get('vat')
            if vat:
                vals['vat'] = vat.strip()
                self._check_duplicate_vat(vals['vat'])
        return super().create(vals_list)

    def write(self, vals):
        vat = vals.get('vat')
        if vat:
            vals['vat'] = vat.strip()
            self._check_duplicate_vat(vals['vat'], exclude_ids=self.ids)
        return super().write(vals)

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
