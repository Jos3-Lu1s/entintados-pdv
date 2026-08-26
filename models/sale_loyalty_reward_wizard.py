# -*- coding: utf-8 -*-

import re
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

class SaleLoyaltyRewWiz(models.TransientModel):
    _inherit = 'sale.loyalty.reward.wizard'

    loyalty_action_type = fields.Selection(
            selection=[
                ('reward', 'Una Promoción'),
                ('discount', 'Descuento de Cliente'),
            ],
            string='¿Qué deseas aplicar?',
            required=True,
        )
    def action_apply_custom(self):
        order_id = self.env.context.get('active_id')
        if order_id:
            order = self.env['sale.order'].browse(order_id)
        else:
            raise ValidationError("No se encontró un id de Orden de venta relacionado.")
        if self.loyalty_action_type == 'reward':
            res=super().action_apply()
            order.order_line.write({'discount': 0})
            return res
        else:
            discount=order.partner_id.discount
            order.order_line.write({'discount': discount*100})
            pass