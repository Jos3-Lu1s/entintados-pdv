from markupsafe import Markup

from odoo import api, fields, models
from odoo.tools.misc import formatLang


class SaleTintWizard(models.TransientModel):
    _name = 'sale.tint.wizard'
    _description = 'Agregar una fórmula de entintado en ventas'

    order_id = fields.Many2one('sale.order', required=True, readonly=True)
    gallery_id = fields.Many2one('tint.gallery', string='Carta de colores')
    color_id = fields.Many2one('tint.color', string='Color')
    formula_id = fields.Many2one('tint.color.formula', string='Fórmula / presentación', required=True)
    base_id = fields.Many2one('product.product', string='Base de pintura', required=True)
    quantity = fields.Integer(string='Número de envases', default=1, required=True)
    base_type_id = fields.Many2one(related='formula_id.base_type_id')
    size_id = fields.Many2one(related='formula_id.size_id')
    line_id = fields.Many2one(related='formula_id.line_scheme_id')
    color_code = fields.Char(related='formula_id.color_id.code', string='Código del color')
    currency_id = fields.Many2one(related='order_id.currency_id')
    unit_price = fields.Monetary(string='Productos por envase', compute='_compute_price')
    total_price = fields.Monetary(string='Productos seleccionados', compute='_compute_price')
    amount_untaxed = fields.Monetary(string='Subtotal', compute='_compute_price')
    amount_tax = fields.Monetary(string='Impuestos', compute='_compute_price')
    amount_total = fields.Monetary(string='Total del entintado', compute='_compute_price')
    preview_html = fields.Html(string='Productos del entintado', compute='_compute_price')
    recipe = fields.Text(string='Fórmula por envase', compute='_compute_recipe')

    @api.depends('formula_id', 'base_id', 'quantity', 'order_id.fiscal_position_id', 'order_id.currency_id')
    def _compute_price(self):
        point = self.env.ref('entintados_pdv.uom_tint_point')
        for wizard in self:
            wizard.unit_price = wizard.total_price = 0
            wizard.amount_untaxed = wizard.amount_tax = wizard.amount_total = 0
            wizard.preview_html = False
            if not wizard.formula_id or not wizard.base_id:
                continue
            items = [(wizard.base_id, wizard.base_id.uom_id, 1)] + [
                (dose.colorant_id, point, dose.points) for dose in wizard.formula_id.line_ids.sorted('sequence')]
            rows = []
            untaxed = total = unit = 0
            quantity = max(wizard.quantity, 0)
            order = wizard.order_id
            money = lambda value: formatLang(wizard.env, value, currency_obj=wizard.currency_id)
            for product, uom, per_container in items:
                price = order._tint_component_price(product, uom)
                taxes = order.fiscal_position_id.map_tax(product.taxes_id._filter_taxes_by_company(order.company_id))
                amounts = taxes.compute_all(price, wizard.currency_id, per_container * quantity,
                                            product=product, partner=order.partner_id)
                unit += price * per_container
                untaxed += amounts['total_excluded']
                total += amounts['total_included']
                rows.append(Markup('<tr><td>%s</td><td class="text-end">%s %s</td>'
                                   '<td class="text-end">%s</td><td class="text-end">%s</td></tr>') % (
                    product.display_name, formatLang(wizard.env, per_container * quantity, digits=4),
                    uom.display_name, money(price), money(amounts['total_excluded'])))
            unit = order._tint_unit_price(wizard.formula_id, wizard.base_id)
            base_taxes = order.fiscal_position_id.map_tax(wizard.base_id.taxes_id._filter_taxes_by_company(order.company_id))
            charged = base_taxes.compute_all(unit, wizard.currency_id, quantity,
                                            product=wizard.base_id, partner=order.partner_id)
            wizard.unit_price = unit
            wizard.total_price = unit * quantity
            wizard.amount_untaxed = charged['total_excluded']
            wizard.amount_total = charged['total_included']
            wizard.amount_tax = charged['total_included'] - charged['total_excluded']
            wizard.preview_html = Markup('<div class="table-responsive"><table class="table table-sm align-middle">'
                '<thead><tr><th>Producto</th><th class="text-end">Cantidad total</th>'
                '<th class="text-end">Precio unitario</th><th class="text-end">Subtotal</th></tr></thead>'
                '<tbody>%s</tbody></table></div>') % Markup('').join(rows)

    @api.depends('formula_id')
    def _compute_recipe(self):
        for wizard in self:
            wizard.recipe = '\n'.join(
                '%s: %s puntos' % (dose.colorant_id.display_name, dose.points)
                for dose in wizard.formula_id.line_ids)

    def _compatible_bases(self):
        self.ensure_one()
        formula = self.formula_id
        if not formula:
            return self.env['product.product']
        domain = [('sale_ok', '=', True), ('tint_role', '=', 'base'),
                  ('tint_base_type_id', '=', formula.base_type_id.id), ('tint_size_id', '=', formula.size_id.id),
                  '|', ('company_id', '=', False), ('company_id', '=', self.order_id.company_id.id)]
        if formula.line_scheme_id:
            domain.append(('lines_product_id', '=', formula.line_scheme_id.id))
        return self.env['product.product'].search(domain)

    @api.onchange('gallery_id')
    def _onchange_gallery(self):
        if self.color_id and self.gallery_id and self.color_id.gallery_id != self.gallery_id:
            self.color_id = self.formula_id = self.base_id = False

    @api.onchange('color_id')
    def _onchange_color(self):
        if self.formula_id.color_id != self.color_id:
            self.formula_id = self.base_id = False
        if self.color_id and not self.formula_id:
            formulas = self.env['tint.color.formula'].search([('color_id', '=', self.color_id.id)], limit=2)
            if len(formulas) == 1:
                self.formula_id = formulas
                self._onchange_formula()

    @api.onchange('formula_id')
    def _onchange_formula(self):
        if self.formula_id:
            self.color_id = self.formula_id.color_id
            self.gallery_id = self.formula_id.gallery_id
        bases = self._compatible_bases()
        if self.base_id not in bases:
            self.base_id = bases if len(bases) == 1 else False

    def action_apply(self):
        self.ensure_one()
        self.order_id._apply_tint(self.formula_id, self.base_id, self.quantity)
        return {'type': 'ir.actions.act_window_close'}
