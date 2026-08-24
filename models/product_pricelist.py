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
