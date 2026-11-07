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
        commercial_lines = self.sale_order_id.order_line.filtered(
            lambda line: not line.display_type and not line.is_downpayment
        )
        lines = commercial_lines.filtered(lambda line: line._is_manual_discount_allowed())
        excluded_lines = commercial_lines - lines

        if not lines:
            raise UserError(_(
                "Ninguna de las líneas de la orden admite descuento manual "
                "(líneas entintadas, precios fijos de acuerdo o anticipos)."
            ))

        discount = self.discount_percentage * 100
        lines.with_context(skip_manual_discount_breakdown=True).write({
            'discount': discount,
            **lines._manual_discount_values(discount),
        })

        if excluded_lines:
            excluded_names = ", ".join(
                excluded_lines.mapped('product_id.display_name') or excluded_lines.mapped('name')
            )
            self.sale_order_id.message_post(
                body=_(
                    "Se aplicó el %(discount)s %% de descuento manual en %(count)d línea(s). "
                    "Se omitieron de la aplicación por no admitir descuento manual: %(excluded)s.",
                    discount=f"{discount:.2f}".rstrip('0').rstrip('.'),
                    count=len(lines),
                    excluded=excluded_names,
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
