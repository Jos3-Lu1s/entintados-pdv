from odoo import fields, models


class TintCategory(models.Model):
    _name = 'tint.category'
    _description = 'Categoría de entintado'
    _order = 'name, id'

    name = fields.Char(string='Nombre', required=True, translate=True)
    active = fields.Boolean(default=True)
    product_category_ids = fields.One2many('product.category', 'tint_category_id', string='Categorías de productos')

    def action_open_sales(self):
        self.ensure_one()
        self.check_access('read')
        self.env['tint.category.sales.report'].check_access('read')
        action = self.env['ir.actions.actions']._for_xml_id('entintados_pdv.action_tint_sales_by_category')
        action['domain'] = [('tint_category_id', '=', self.id)]
        action['context'] = {'search_default_by_color': 1}
        return action


class ProductCategory(models.Model):
    _inherit = 'product.category'

    tint_category_id = fields.Many2one('tint.category', string='Categoría de entintado', ondelete='restrict', index=True)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    tint_category_id = fields.Many2one(related='categ_id.tint_category_id', string='Categoría de entintado', store=True)
