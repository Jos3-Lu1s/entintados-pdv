from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    sale_price_unit_editable = fields.Boolean(
        string="Permitir editar el precio unitario",
        default=True,
        help="Si se desactiva, solo los usuarios del grupo «Editar precio unitario en ventas» "
        "pueden cambiar el precio unitario de las líneas del pedido.",
    )
    sale_discount_editable = fields.Boolean(
        string="Permitir editar el descuento",
        default=True,
        help="Si se desactiva, solo los usuarios del grupo «Editar descuento en ventas» "
        "pueden cambiar el descuento de las líneas y usar el botón «Descuento» del pedido.",
    )
