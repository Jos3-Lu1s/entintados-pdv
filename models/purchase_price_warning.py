from collections import defaultdict

from odoo import api, fields, models
from odoo.tools.safe_eval import safe_eval


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    sale_cost_warning = fields.Char(compute='_compute_sale_cost_warning', string='Advertencia de margen')

    @api.model
    def _recent_product_purchase_cost_domain(self):
        action = self.env.ref('entintados_pdv.action_product_purchase_costs')
        lines = self.search_fetch(
            safe_eval(action.domain), ['product_id', 'order_id'],
            order='date_approve desc nulls last, id desc',
        )
        orders_by_product = defaultdict(set)
        recent_line_ids = []
        for line in lines:
            orders = orders_by_product[line.product_id.id]
            order_id = line.order_id.id
            if order_id in orders or len(orders) < 3:
                orders.add(order_id)
                recent_line_ids.append(line.id)
        return [('id', 'in', recent_line_ids)]

    @api.depends('product_id', 'price_unit', 'discount', 'tax_ids', 'product_uom_id',
                 'product_qty', 'order_id.currency_id', 'order_id.date_order',
                 'order_id.company_id', 'product_id.lst_price', 'product_id.taxes_id')
    def _compute_sale_cost_warning(self):
        for line in self:
            line.sale_cost_warning = False
            if not line.product_id or line.display_type or not line.product_uom_id:
                continue
            company = line.order_id.company_id or self.env.company
            currency = line.order_id.currency_id or company.currency_id
            product = line.product_id.with_company(company)
            purchase = line.tax_ids.compute_all(
                line.price_unit * (1 - line.discount / 100), currency=currency,
                quantity=1, product=product, partner=line.order_id.partner_id,
            )['total_excluded']
            purchase = line.product_uom_id._compute_price(purchase, product.uom_id)
            purchase = currency._convert(purchase, company.currency_id, company,
                                        fields.Date.to_date(line.order_id.date_order) or fields.Date.context_today(line))
            sale = product.taxes_id.filtered(lambda t: t.company_id == company).compute_all(
                product.lst_price, currency=company.currency_id, quantity=1,
                product=product,
            )['total_excluded']
            if company.currency_id.compare_amounts(purchase, sale) > 0:
                line.sale_cost_warning = (
                    '%s: costo de compra %.2f mayor al precio de venta %.2f %s por %s (sin impuestos).'
                    % (product.display_name, purchase, sale, company.currency_id.name, product.uom_id.display_name))

    @api.onchange('product_id', 'price_unit', 'discount', 'tax_ids', 'product_uom_id')
    def _onchange_sale_cost_warning(self):
        warnings = self.mapped('sale_cost_warning')
        if any(warnings):
            return {'warning': {'title': 'Costo mayor al precio de venta',
                                'message': '\n'.join(filter(None, warnings))}}


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    sale_cost_warning = fields.Text(compute='_compute_sale_cost_warning', string='Advertencias de compra')

    @api.depends('order_line.sale_cost_warning')
    def _compute_sale_cost_warning(self):
        for order in self:
            order.sale_cost_warning = '\n'.join(filter(None, order.order_line.mapped('sale_cost_warning')))
