from odoo import models


class SaleOrderDiscount(models.TransientModel):
    _inherit = 'sale.order.discount'

    def action_apply_discount(self):
        # "En todas las líneas": el % va como descuento adicional, en cascada con la tarifa y el
        # acuerdo, y nunca en líneas de precio fijo, recompensa, descuento o sección/nota.
        self.ensure_one()
        if self.discount_type != 'sol_discount':
            return super().action_apply_discount()
        self = self.with_company(self.company_id)
        lines = self.sale_order_id.order_line.filtered(lambda line: line._is_extra_discount_allowed())
        lines.write({'extra_discount': self.discount_percentage * 100})
