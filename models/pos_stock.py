from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class ProductProduct(models.Model):
    _inherit = 'product.product'

    pos_stock_qty = fields.Float(compute='_compute_pos_stock_qty', string='Existencias TPV')

    @api.depends('free_qty', 'is_storable')
    @api.depends_context('location', 'company')
    def _compute_pos_stock_qty(self):
        for product in self:
            product.pos_stock_qty = product.free_qty if product.type == 'consu' and product.is_storable else 0

    @api.model
    def _load_pos_data_fields(self, config):
        return list(dict.fromkeys(super()._load_pos_data_fields(config) + ['is_storable', 'pos_stock_qty']))

    @api.model
    def _load_pos_data_read(self, records, config):
        records = records.with_company(config.company_id).with_context(
            location=config.picking_type_id.default_location_src_id.id)
        return super()._load_pos_data_read(records, config)

    def get_pos_stock(self, config_id):
        config = self.env['pos.config'].browse(config_id)
        config.check_access('read')
        self.check_access('read')
        products = self.with_company(config.company_id).with_context(
            location=config.picking_type_id.default_location_src_id.id)
        return {p.id: p.free_qty for p in products if p.type == 'consu' and p.is_storable}


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    pos_stock_qty = fields.Float(compute='_compute_pos_stock_qty', string='Existencias TPV')

    @api.depends('product_variant_ids.pos_stock_qty')
    @api.depends_context('location', 'company')
    def _compute_pos_stock_qty(self):
        for product in self:
            product.pos_stock_qty = sum(product.product_variant_ids.mapped('pos_stock_qty'))

    @api.model
    def _load_pos_data_fields(self, config):
        return list(dict.fromkeys(super()._load_pos_data_fields(config) + ['is_storable', 'pos_stock_qty']))

    @api.model
    def _load_pos_data_read(self, records, config):
        records = records.with_company(config.company_id).with_context(
            location=config.picking_type_id.default_location_src_id.id)
        return super()._load_pos_data_read(records, config)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _check_pos_stock(self):
        for order in self:
            quantities = defaultdict(float)
            for line in order.lines.filtered(
                    lambda l: l.qty > 0 and l.product_id.type == 'consu' and l.product_id.is_storable):
                quantities[line.product_id] += line.qty
            for product, quantity in quantities.items():
                stock_product = product.with_company(order.company_id).with_context(
                    location=order.config_id.picking_type_id.default_location_src_id.id)
                if float_compare(quantity, stock_product.free_qty,
                                 precision_rounding=product.uom_id.rounding) > 0:
                    raise UserError(_(
                        'Existencias insuficientes para %(product)s. Disponibles: %(stock)s; solicitadas: %(qty)s.',
                        product=product.display_name, stock=stock_product.free_qty, qty=quantity))

    def action_pos_order_paid(self):
        self.ensure_one()
        if not self.picking_ids:
            self._check_pos_stock()
        return super().action_pos_order_paid()
