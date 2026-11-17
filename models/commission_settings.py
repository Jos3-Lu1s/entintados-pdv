from odoo import fields, models


class CommissionRange(models.Model):
    _name = 'commission.range'
    _description = 'Rango de comisión'
    _rec_name = 'margin_from'

    margin_from = fields.Float(string='Margen inicial', required=True)
    margin_to = fields.Float(string='Margen final', required=True)
    percentage = fields.Float(string='Porcentaje', required=True)


class CommissionGoal(models.Model):
    _name = 'commission.goal'
    _description = 'Meta por vendedor'
    _rec_name = 'seller_id'

    seller_id = fields.Many2one('res.users', domain=[('share', '=', False)], string='Vendedor', required=True)
    minimum = fields.Float(string='Mínimo', required=True)
    scope = fields.Float(string='Alcance', required=True)
    commercial_goal = fields.Monetary(string='Meta comercial', required=True)
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id, required=True)
