from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    sale_price_unit_editable = fields.Boolean(
        related="company_id.sale_price_unit_editable", readonly=False
    )
    sale_discount_editable = fields.Boolean(
        related="company_id.sale_discount_editable", readonly=False
    )
