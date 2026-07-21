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
        help="Marca este contacto como acreedor (a quien se le debe dinero).",
    )
    is_supplier = fields.Boolean(
        string="Proveedor",
        help="Marca este contacto como proveedor de bienes o servicios.",
    )
    is_distributor = fields.Boolean(
        string="Distribuidor",
        help="Marca este contacto como distribuidor autorizado.",
    )

    @api.model
    def _load_pos_data_domain(self, data, config):
        # EXTENDS point_of_sale
        # Además de los partners ya requeridos por el core (los de órdenes
        # cargadas y el propio cajero), solo precargar contactos marcados
        # como clientes.
        domain = super()._load_pos_data_domain(data, config)
        return domain + [('is_customer', '=', True)]

    @api.model
    def get_new_partner(self, config_id, domain, offset):
        # OVERRIDES point_of_sale (no se puede usar super() a medias: la
        # restricción is_customer debe aplicarse en las dos ramas de
        # búsqueda del método original). Revisar este override si el core
        # cambia la lógica de `get_new_partner` en una futura versión.
        config = self.env['pos.config'].browse(config_id)
        customer_domain = [('is_customer', '=', True)]
        if len(domain) == 0:
            limited_partner_ids = {
                partner[0] for partner in config.get_limited_partners_loading(offset)
            }
            new_partners = self.search([('id', 'in', list(limited_partner_ids))] + customer_domain)
        else:
            # If search domain is not empty, we need to search inside all partners
            new_partners = self.search(domain + customer_domain, offset=offset, limit=100)
        fiscal_positions = new_partners.fiscal_position_id
        return {
            'res.partner': self._load_pos_data_read(new_partners, config),
            'account.fiscal.position': self.env['account.fiscal.position']._load_pos_data_read(fiscal_positions, config),
        }
