from odoo import api, Command, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare


class ProductStockLookup(models.TransientModel):
    _name = 'product.stock.lookup'
    _description = 'Existencias de un producto por almacén'
    _check_company_auto = True

    product_tmpl_id = fields.Many2one('product.template', string='Plantilla', readonly=True)
    product_id = fields.Many2one('product.product', string='Producto', required=True, check_company=True)
    product_uom_id = fields.Many2one(related='product_id.uom_id', string='Unidad')
    company_id = fields.Many2one('res.company', string='Compañía', required=True, default=lambda self: self.env.company)
    sale_order_id = fields.Many2one('sale.order', string='Orden de venta', readonly=True, check_company=True)
    order_product_ids = fields.Many2many('product.product', compute='_compute_order_products')
    requested_qty = fields.Float(string='Cantidad pendiente de la orden', digits='Product Unit', compute='_compute_order_products')
    order_warehouse_id = fields.Many2one(related='sale_order_id.warehouse_id', string='Almacén de la orden')
    warehouse_id = fields.Many2one('stock.warehouse', string='Almacén', check_company=True)
    only_available = fields.Boolean(string='Solo con disponibilidad')
    line_ids = fields.One2many('product.stock.lookup.line', 'wizard_id', compute='_compute_stock', compute_sudo=False)
    total_on_hand = fields.Float(string='Existencia física', digits='Product Unit', compute='_compute_stock')
    total_reserved = fields.Float(string='Reservado', digits='Product Unit', compute='_compute_stock')
    total_available = fields.Float(string='Disponible', digits='Product Unit', compute='_compute_stock')

    @api.depends('sale_order_id.order_line.product_id', 'sale_order_id.order_line.product_uom_qty',
                 'sale_order_id.order_line.qty_delivered', 'product_id')
    def _compute_order_products(self):
        for wizard in self:
            lines = wizard.sale_order_id.order_lines_for_stock_check() if wizard.sale_order_id else self.env['sale.order.line']
            wizard.order_product_ids = lines.product_id.filtered('is_storable')
            wizard.requested_qty = sum(line.product_uom_id._compute_quantity(
                max(line.product_uom_qty - line.qty_delivered, 0), wizard.product_uom_id, round=False)
                for line in lines if wizard.product_id and line.product_id == wizard.product_id)

    def _check_scope(self):
        self.ensure_one()
        if self.company_id not in self.env.companies:
            raise AccessError(_('Selecciona una compañía habilitada en tu sesión.'))
        if self.product_id:
            self.product_id.check_access('read')
            if not self.product_id.is_storable:
                raise UserError(_('La consulta de existencias es para productos inventariables.'))
            if self.product_id.company_id and self.product_id.company_id != self.company_id:
                raise ValidationError(_('El producto pertenece a otra compañía.'))
            if self.product_tmpl_id and self.product_id.product_tmpl_id != self.product_tmpl_id:
                raise ValidationError(_('Selecciona una variante de la plantilla consultada.'))
        if self.warehouse_id:
            self.warehouse_id.check_access('read')
            if self.warehouse_id.company_id != self.company_id:
                raise ValidationError(_('El almacén pertenece a otra compañía.'))
        if self.sale_order_id:
            self.sale_order_id.check_access('read')
            if self.product_id and self.product_id not in self.sale_order_id.order_line.product_id:
                raise ValidationError(_('Selecciona uno de los productos de esta orden de venta.'))

    @api.constrains('product_id', 'product_tmpl_id', 'company_id', 'warehouse_id', 'sale_order_id')
    def _check_lookup_scope(self):
        for wizard in self:
            wizard._check_scope()

    @api.onchange('company_id')
    def _onchange_company(self):
        if self.warehouse_id.company_id != self.company_id:
            self.warehouse_id = False
        if self.product_id.company_id and self.product_id.company_id != self.company_id:
            self.product_id = False

    @api.depends('product_id', 'company_id', 'warehouse_id', 'only_available')
    @api.depends_context('allowed_company_ids')
    def _compute_stock(self):
        for wizard in self:
            wizard.line_ids = [Command.clear()]
            wizard.total_on_hand = wizard.total_reserved = wizard.total_available = 0
            if not wizard.product_id or not wizard.company_id:
                continue
            wizard._check_scope()
            warehouses = wizard.warehouse_id or self.env['stock.warehouse'].search([
                ('company_id', '=', wizard.company_id.id)], order='name, id')
            commands = []
            for warehouse in warehouses:
                amounts = self.env['stock.quant']._read_group([
                    ('product_id', '=', wizard.product_id.id), ('company_id', '=', wizard.company_id.id),
                    ('location_id', 'child_of', warehouse.view_location_id.id),
                    ('location_id.usage', '=', 'internal'),
                ], [], ['quantity:sum', 'reserved_quantity:sum'])
                on_hand, reserved = amounts[0] if amounts else (0, 0)
                on_hand, reserved = on_hand or 0, reserved or 0
                available = on_hand - reserved
                if wizard.only_available and float_compare(available, 0,
                        precision_rounding=wizard.product_uom_id.rounding) <= 0:
                    continue
                commands.append(Command.create({'warehouse_id': warehouse.id,
                    'on_hand': on_hand, 'reserved': reserved, 'available': available}))
                wizard.total_on_hand += on_hand
                wizard.total_reserved += reserved
                wizard.total_available += available
            wizard.line_ids = commands

    def action_refresh(self):
        self.ensure_one()
        self._compute_stock()
        return {'type': 'ir.actions.act_window', 'name': _('Existencias por almacén'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new',
                'context': dict(self.env.context, dialog_size='medium')}


class ProductStockLookupLine(models.TransientModel):
    _name = 'product.stock.lookup.line'
    _description = 'Cantidades del producto por almacén'
    _order = 'id'

    wizard_id = fields.Many2one('product.stock.lookup', required=True, ondelete='cascade')
    warehouse_id = fields.Many2one('stock.warehouse', string='Almacén', readonly=True)
    on_hand = fields.Float(string='Existencia física', digits='Product Unit', readonly=True)
    reserved = fields.Float(string='Reservado', digits='Product Unit', readonly=True)
    available = fields.Float(string='Disponible', digits='Product Unit', readonly=True)


class ProductStockLookupProduct(models.Model):
    _inherit = 'product.product'

    def action_view_stock_by_warehouse(self):
        self.ensure_one()
        return self._open_stock_lookup()

    def _open_stock_lookup(self, company=None, order=None):
        self.ensure_one()
        self.check_access('read')
        if not self.is_storable:
            raise UserError(_('Selecciona un producto inventariable.'))
        return {'type': 'ir.actions.act_window', 'name': _('Existencias por almacén'),
            'res_model': 'product.stock.lookup', 'view_mode': 'form', 'target': 'new',
            'context': {'default_product_id': self.id, 'default_product_tmpl_id': self.product_tmpl_id.id,
                'default_company_id': (company or self.company_id or self.env.company).id,
                'default_sale_order_id': order.id if order else False, 'dialog_size': 'medium'}}


class ProductStockLookupTemplate(models.Model):
    _inherit = 'product.template'

    def action_view_stock_by_warehouse(self):
        self.ensure_one()
        self.check_access('read')
        if not self.is_storable:
            raise UserError(_('Selecciona un producto inventariable.'))
        if len(self.product_variant_ids) == 1:
            return self.product_variant_ids._open_stock_lookup()
        return {'type': 'ir.actions.act_window', 'name': _('Existencias por almacén'),
            'res_model': 'product.stock.lookup', 'view_mode': 'form', 'target': 'new',
            'context': {'default_product_tmpl_id': self.id,
                'default_company_id': (self.company_id or self.env.company).id, 'dialog_size': 'medium'}}


class ProductStockLookupSaleLine(models.Model):
    _inherit = 'sale.order.line'

    def action_view_stock_by_warehouse(self):
        self.ensure_one()
        self.check_access('read')
        if not self.product_id:
            raise UserError(_('Selecciona primero el producto de esta línea.'))
        return self.product_id._open_stock_lookup(company=self.company_id, order=self.order_id)


class ProductStockLookupSaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_check_branch_stock(self):
        self.ensure_one()
        self.check_access('read')
        products = self.order_lines_for_stock_check().product_id.filtered('is_storable')
        if not products:
            raise UserError(_('Agrega al menos un producto inventariable a la orden para consultar su disponibilidad.'))
        return {'type': 'ir.actions.act_window', 'name': _('Disponibilidad por sucursal'),
            'res_model': 'product.stock.lookup', 'view_mode': 'form', 'target': 'new',
            'context': {'default_sale_order_id': self.id, 'default_company_id': self.company_id.id,
                'default_product_id': products.id if len(products) == 1 else False,
                'default_product_tmpl_id': False, 'dialog_size': 'medium'}}
