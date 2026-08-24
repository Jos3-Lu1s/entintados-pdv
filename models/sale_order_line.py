# -*- coding: utf-8 -*-

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.depends(
        'product_id',
        'product_uom_id',
        'product_uom_qty',
        'order_id.partner_id',
        'order_id.partner_id.discount',
    )
    def _compute_discount(self):
        # Primero dejamos que Odoo haga su cálculo normal
        super()._compute_discount()

        # Después aplicamos el descuento del cliente
        for line in self:
            if (
                line.product_id
                and not line.display_type
                and line.order_id.partner_id
            ):
                line.discount = line.order_id.partner_id.discount*100