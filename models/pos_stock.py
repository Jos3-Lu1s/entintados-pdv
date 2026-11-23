from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
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

    @api.model
    def get_pos_quotation_branch_stock(self, config_id, quantities, tint_points=None):
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Se requiere acceso al Punto de Venta.'))
        config = self.env['pos.config'].browse(config_id).exists()
        if not config or config.company_id not in self.env.companies:
            raise AccessError(_('Selecciona un Punto de Venta de una compañía habilitada.'))
        config.check_access('read')
        tint_points = tint_points or {}
        product_ids = {int(product_id) for product_id in quantities.keys() | tint_points.keys()}
        products = self.browse(sorted(product_ids)).exists()
        products.check_access('read')
        products = products.filtered(lambda product: product.is_storable
                                     and (not product.company_id or product.company_id in config.company_id.parent_ids))
        point = self.env.ref('entintados_pdv.uom_tint_point')
        required_by_product = {}
        for product in products:
            key = str(product.id)
            required = float(quantities.get(key, 0))
            if key in tint_points:
                if product.tint_role != 'colorant' or not product.uom_id._has_common_reference(point):
                    raise UserError(_('Revisa la unidad del colorante %s.') % product.display_name)
                required += point._compute_quantity(float(tint_points[key]), product.uom_id, round=False)
            required_by_product[product.id] = required
        if set(map(int, tint_points)) - required_by_product.keys():
            raise UserError(_('No se pudo consultar todos los colorantes de la fórmula.'))
        companies = self.env.user.company_ids.filtered(
            lambda company: company.root_id == config.company_id.root_id)
        warehouses = self.env['stock.warehouse'].sudo().search(
            [('company_id', 'in', companies.ids)], order='name, id')
        rows = []
        for warehouse in warehouses:
            lookup = self.env['product.stock.lookup'].new({'company_id': warehouse.company_id.id})
            applicable = products.filtered(lambda product: not product.company_id
                                          or product.company_id in warehouse.company_id.parent_ids)
            stock = lookup._warehouse_component_stock(warehouse, applicable.ids, self.env['stock.quant'].sudo())
            for product in products:
                required = required_by_product[product.id]
                on_hand, reserved = stock.get(product.id, (0, 0))
                available = on_hand - reserved
                rows.append({
                    'warehouse': warehouse.display_name, 'product': product.display_name,
                    'uom': product.uom_id.name, 'required': required, 'onHand': on_hand,
                    'reserved': reserved, 'available': available,
                    'shortage': product.uom_id.compare(required, available) > 0,
                })
        return rows


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
                quantities[line.product_id] += line.product_uom_id._compute_quantity(
                    line.qty, line.product_id.uom_id, round=False)
            for product, quantity in quantities.items():
                stock_product = product.with_company(order.company_id).with_context(
                    location=order.config_id.picking_type_id.default_location_src_id.id)
                if stock_product.free_qty <= 0 or float_compare(quantity, stock_product.free_qty,
                                 precision_rounding=product.uom_id.rounding) > 0:
                    raise UserError(_(
                        'Existencias insuficientes para %(product)s. Disponibles: %(stock)s; solicitadas: %(qty)s.',
                        product=product.display_name, stock=stock_product.free_qty, qty=quantity))

    def action_pos_order_paid(self):
        self.ensure_one()
        if not self.picking_ids:
            self._check_pos_stock()
        return super().action_pos_order_paid()

    def _process_saved_order(self, draft):
        if not draft and not self.picking_ids:
            self._check_pos_stock()
        return super()._process_saved_order(draft)
