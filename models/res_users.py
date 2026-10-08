from odoo import models, fields

class ResUsers(models.Model):
    _inherit = 'res.users'

    digital_signature = fields.Binary(
        string="Firma Digital para Aprobaciones",
        copy=False,
        attachment=True,
        help="Firma digital del gerente/usuario utilizada para la autorización automática de documentos y salidas de almacén.",
    )
