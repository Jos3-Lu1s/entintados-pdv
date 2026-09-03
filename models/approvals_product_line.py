from odoo import models, fields, api


class ApprovalsProductLine(models.Model):
    _inherit = 'approval.product.line'

    description = fields.Text('Description')