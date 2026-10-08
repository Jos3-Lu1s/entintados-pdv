
# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..utils.points import format_points

class PricelistProductWizard(models.TransientModel):
    _name = 'product.pricelist.wizard'
    _description = 'Agregar productos a lista de precios'

    pricelist_id = fields.Many2one(
        'product.pricelist',
        string='Lista de precios',
        required=True,
    )

    apply_by = fields.Selection(
        selection=[
            ('esquema', 'Esquema'),
            ('linea', 'Línea'),
        ],
        string='Agregar por',
        required=True,
        default='esquema',
    )

    action_type = fields.Selection(
        selection=[
            ('add', 'Añadir'),
            ('replace', 'Reemplazar'),
        ],
        string='Necesitas',
        required=True,
        default='add',
    )

    esquema_id = fields.Many2one(
        'tint.schema',
        string='Esquema',
    )

    linea_id = fields.Many2one(
        'lines.product',
        string='Línea',
    )

    @api.onchange('apply_by')
    def _onchange_apply_by(self):
        if self.apply_by == 'esquema':
            self.linea_id = False
        elif self.apply_by == 'linea':
            self.esquema_id = False



    def _get_target_products(self):
        self.ensure_one()
        if self.apply_by == 'esquema' and self.esquema_id:
            return self.env['product.template'].search([
                ('scheme_id', '=', self.esquema_id.id),
            ])
        elif self.apply_by == 'linea' and self.linea_id:
            return self.env['product.template'].search([
                ('lines_product_id', '=', self.linea_id.id),
            ])
        return self.env['product.template']


    def action_add_products(self):
        self.ensure_one()

        products = self._get_target_products()
        if not products:
            raise ValidationError("No se encontraron productos para el filtro seleccionado.")

        existing_items = self.env['product.pricelist.item'].search([
            ('pricelist_id', '=', self.pricelist_id.id),
            ('applied_on', '=', '1_product'),
        ])
      
        if self.action_type == 'replace':
            if len(existing_items) <= 1:
                raise ValidationError(
                    "'Reemplazar' solo está disponible cuando ya existe más de un ítem para este filtro."
                )
            existing_items.unlink()
            products_to_add = products
        else:  # 'add'
            products_with_rule = existing_items.mapped('product_tmpl_id')
            products_to_add = products - products_with_rule

        vals_list = [{
            'pricelist_id': self.pricelist_id.id,
            'applied_on': '1_product',
            'product_tmpl_id': product.id,
            'compute_price': 'fixed',
            'fixed_price': product.list_price,
        } for product in products_to_add]

        if vals_list:
            self.env['product.pricelist.item'].create(vals_list)

        return {'type': 'ir.actions.act_window_close'}