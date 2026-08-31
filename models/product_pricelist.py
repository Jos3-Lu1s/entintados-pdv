# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..utils.points import format_points

class ProductPricelist(models.Model):
    _inherit='product.pricelist'


    partner_ids = fields.Many2many(
        'res.partner',
        string="Contactos",
        compute='_compute_partner_ids',
        inverse='_inverse_partner_ids',
    )
    partner_count = fields.Integer(
        string="Núm. de contactos",
        compute='_compute_partner_ids',
    )

    show_in_pos = fields.Boolean(
        string="Disponible en Punto de Venta",
        default=False,
        help="Si está marcado, esta lista de precios se agregará automáticamente "
             "como disponible en los Puntos de Venta compatibles (misma compañía y moneda).",
    )


    def _compute_partner_ids(self):
        all_partners = self.env['res.partner'].search([])
        for pricelist in self:
            partners = all_partners.filtered(
                lambda p: p.property_product_pricelist == pricelist
            )
            pricelist.partner_ids = partners
            pricelist.partner_count = len(partners)
    
    def _inverse_partner_ids(self):
        pass

    def action_open_product_wizard(self):
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': 'Agregar productos',
            'res_model': 'product.pricelist.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_pricelist_id': self.id,
            },
        }

    def _add_to_pos_configs(self):
        for pricelist in self:
            if not pricelist.show_in_pos:
                continue

            domain = [('company_id', 'in', [False, pricelist.company_id.id])] if pricelist.company_id else []
            pos_configs = self.env['pos.config'].search(domain)

            for config in pos_configs:
                if pricelist.company_id and pricelist.company_id != config.company_id:
                    continue
                if config.use_pricelist and pricelist.currency_id != config.currency_id:
                    continue
                if pricelist not in config.available_pricelist_ids:
                    config.available_pricelist_ids = [(4, pricelist.id)]

    def _remove_from_pos_configs(self):
        pos_configs = self.env['pos.config'].search([('available_pricelist_ids', 'in', self.ids)])
        for config in pos_configs:
            config.available_pricelist_ids = [(3, pricelist.id) for pricelist in self]

    @api.model_create_multi
    def create(self, vals_list):
        pricelists = super().create(vals_list)
        pricelists._add_to_pos_configs()
        return pricelists

    def write(self, vals):
        res = super().write(vals)
        if 'show_in_pos' in vals:
            if vals['show_in_pos']:
                self._add_to_pos_configs()
            else:
                self._remove_from_pos_configs()
        return res
