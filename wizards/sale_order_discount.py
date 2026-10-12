from odoo import _, models
from odoo.exceptions import UserError


class SaleOrderDiscount(models.TransientModel):
    _inherit = 'sale.order.discount'

    def action_apply_discount(self):
        # El % sustituye al descuento de cada línea y queda como manual, también con 0 %.
        # Nunca toca precio fijo, recompensas, producto de descuento, anticipos ni secciones/notas.
        # "Descuento global" e "Importe fijo" se rechazan porque agregan líneas al pedido.
        self.ensure_one()
        if self.discount_type != 'sol_discount':
            raise UserError(_("El descuento solo puede aplicarse en las líneas de la orden."))
        self = self.with_company(self.company_id)
        lines = self.sale_order_id.order_line.filtered(lambda line: line._is_manual_discount_allowed())
        discount = self.discount_percentage * 100
        lines.with_context(skip_manual_discount_breakdown=True).write({
            'discount': discount,
            **lines._manual_discount_values(discount),
        })
