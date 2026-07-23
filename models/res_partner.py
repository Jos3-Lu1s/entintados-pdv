# -*- coding: utf-8 -*-

from odoo import api, fields, models


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
