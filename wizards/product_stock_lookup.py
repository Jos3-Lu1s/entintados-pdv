from odoo import api, Command, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError


class ProductStockLookup(models.TransientModel):
    _name = 'product.stock.lookup'
    _description = 'Existencias de un producto por almacén'
    _check_company_auto = True

    product_tmpl_id = fields.Many2one('product.template', string='Plantilla', readonly=True)
    product_id = fields.Many2one('product.product', string='Producto', required=True, check_company=True)
    product_uom_id = fields.Many2one(related='product_id.uom_id', string='Unidad')
    company_id = fields.Many2one('res.company', string='Compañía', required=True, default=lambda self: self.env.company)
    sale_order_id = fields.Many2one('sale.order', string='Orden de venta', readonly=True, check_company=True)
    sale_line_id = fields.Many2one('sale.order.line', string='Producto / entintado', check_company=True)
    order_product_ids = fields.Many2many('product.product', compute='_compute_order_products')
    requested_qty = fields.Float(string='Cantidad pendiente de la orden', digits='Product Unit', compute='_compute_order_products')
    order_warehouse_id = fields.Many2one(related='sale_order_id.warehouse_id', string='Almacén de la orden')
    warehouse_id = fields.Many2one('stock.warehouse', string='Almacén', check_company=True)
    has_tint = fields.Boolean(compute='_compute_stock')
    line_ids = fields.One2many('product.stock.lookup.line', 'wizard_id', compute='_compute_stock', compute_sudo=False)
    total_on_hand = fields.Float(string='Existencia física', digits='Product Unit', compute='_compute_stock')
    total_reserved = fields.Float(string='Reservado', digits='Product Unit', compute='_compute_stock')
    total_available = fields.Float(string='Disponible', digits='Product Unit', compute='_compute_stock')

    @api.depends('sale_order_id.order_line.product_id', 'sale_order_id.order_line.product_uom_qty',
                 'sale_order_id.order_line.qty_delivered', 'sale_order_id.order_line.product_uom_id', 'product_id', 'sale_line_id')
    def _compute_order_products(self):
        for wizard in self:
            lines = wizard.sale_order_id.order_lines_for_stock_check() if wizard.sale_order_id else self.env['sale.order.line']
            wizard.order_product_ids = lines.product_id.filtered('is_storable')
            if wizard.sale_line_id:
                lines &= wizard.sale_line_id
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
        if self.sale_line_id:
            self.sale_line_id.check_access('read')
            if (self.sale_line_id.order_id != self.sale_order_id
                    or self.sale_line_id.product_id != self.product_id
                    or self.sale_line_id not in self.sale_order_id.order_lines_for_stock_check()):
                raise ValidationError(_('Selecciona un producto o entintado de esta orden de venta.'))

    @api.constrains('product_id', 'product_tmpl_id', 'company_id', 'warehouse_id', 'sale_order_id', 'sale_line_id')
    def _check_lookup_scope(self):
        for wizard in self:
            wizard._check_scope()

    @api.onchange('sale_line_id')
    def _onchange_sale_line(self):
        if self.sale_order_id:
            self.product_id = self.sale_line_id.product_id

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            line_id = values.get('sale_line_id', self.env.context.get('default_sale_line_id'))
            if line_id:
                values['product_id'] = self.env['sale.order.line'].browse(line_id).product_id.id
        return super().create(vals_list)

    def write(self, values):
        if values.get('sale_line_id'):
            values = dict(values, product_id=self.env['sale.order.line'].browse(values['sale_line_id']).product_id.id)
        return super().write(values)

    @api.onchange('company_id')
    def _onchange_company(self):
        if self.warehouse_id.company_id != self.company_id:
            self.warehouse_id = False
        if self.product_id.company_id and self.product_id.company_id != self.company_id:
            self.product_id = False

    def _lookup_components(self):
        """Read the frozen order recipe; never rebuild it from today's formula."""
        self.ensure_one()
        lines = self.sale_order_id.order_lines_for_stock_check().filtered(
            lambda line: line.product_id == self.product_id) if self.sale_order_id else self.env['sale.order.line']
        if self.sale_line_id:
            lines &= self.sale_line_id
        tinted = lines.filtered(lambda line: line._tint_recipe().get('base_id') == self.product_id.id)
        components = {self.product_id: {'quantity': sum(line.product_uom_id._compute_quantity(
            max(line.product_uom_qty - line.qty_delivered, 0), self.product_uom_id, round=False)
            for line in lines), 'kind': 'base' if tinted else 'product', 'colors': []}}
        if not tinted:
            return False, components
        point = self.env.ref('entintados_pdv.uom_tint_point')

        def add(product, quantity, color):
            product.check_access('read')
            if product.company_id and product.company_id != self.company_id:
                raise ValidationError(_('Un componente del entintado pertenece a otra compañía.'))
            item = components.setdefault(product, {'quantity': 0, 'kind': 'colorant', 'colors': []})
            item['quantity'] += quantity
            if color and color not in item['colors']:
                item['colors'].append(color)

        for line in tinted:
            recipe = line._tint_recipe()
            color = self.sale_order_id._tint_color_description(recipe)
            if color not in components[self.product_id]['colors']:
                components[self.product_id]['colors'].append(color)
            pending = max(line.product_uom_qty - line.qty_delivered, 0)
            for dose in recipe.get('doses', []):
                product = self.env['product.product'].browse(dose['product_id']).exists()
                if not product:
                    raise UserError(_('No se encontró un colorante de la receta guardada.'))
                add(product, point._compute_quantity(dose['points'] * pending, product.uom_id, round=False), color)
        return True, components

    @api.depends('product_id', 'company_id', 'warehouse_id', 'sale_order_id', 'sale_line_id',
                 'sale_order_id.order_line.product_id', 'sale_order_id.order_line.product_uom_qty',
                 'sale_order_id.order_line.product_uom_id',
                 'sale_order_id.order_line.qty_delivered', 'sale_order_id.order_line.tint_snapshot')
    @api.depends_context('allowed_company_ids')
    def _compute_stock(self):
        for wizard in self:
            wizard.line_ids = [Command.clear()]
            wizard.total_on_hand = wizard.total_reserved = wizard.total_available = 0
            wizard.has_tint = False
            if not wizard.product_id or not wizard.company_id:
                continue
            wizard._check_scope()
            wizard.has_tint, components = wizard._lookup_components()
            warehouses = wizard.warehouse_id or self.env['stock.warehouse'].search([
                ('company_id', '=', wizard.company_id.id)], order='name, id')
            commands = []
            for warehouse in warehouses:
                amounts = self.env['stock.quant']._read_group([
                    ('product_id', 'in', [product.id for product in components]), ('company_id', '=', wizard.company_id.id),
                    ('location_id', 'child_of', warehouse.view_location_id.id),
                    ('location_id.usage', '=', 'internal'),
                ], ['product_id'], ['quantity:sum', 'reserved_quantity:sum'])
                stock = {product.id: (quantity or 0, reserved or 0) for product, quantity, reserved in amounts}
                for product, component in components.items():
                    on_hand, reserved = stock.get(product.id, (0, 0))
                    available = on_hand - reserved
                    commands.append(Command.create({'warehouse_id': warehouse.id, 'product_id': product.id,
                        'component_kind': component['kind'], 'color_names': ', '.join(filter(None, component['colors'])),
                        'required_qty': component['quantity'], 'on_hand': on_hand, 'reserved': reserved,
                        'available': available, 'shortage': product.uom_id.compare(component['quantity'], available) > 0}))
                    # Existing totals always describe the selected product, never mixed units.
                    if product == wizard.product_id:
                        wizard.total_on_hand += on_hand
                        wizard.total_reserved += reserved
                        wizard.total_available += available
            wizard.line_ids = commands

    def action_refresh(self):
        self.ensure_one()
        self._compute_stock()
        return {'type': 'ir.actions.act_window', 'name': _('Existencias por almacén'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new',
                'context': dict(self.env.context, dialog_size='large' if self.has_tint else 'medium')}


class ProductStockLookupLine(models.TransientModel):
    _name = 'product.stock.lookup.line'
    _description = 'Cantidades del producto por almacén'
    _order = 'id'

    wizard_id = fields.Many2one('product.stock.lookup', required=True, ondelete='cascade')
    warehouse_id = fields.Many2one('stock.warehouse', string='Almacén', readonly=True)
    product_id = fields.Many2one('product.product', string='Componente', readonly=True)
    product_uom_id = fields.Many2one(related='product_id.uom_id', string='Unidad')
    component_kind = fields.Selection([('product', 'Producto'), ('base', 'Base'), ('colorant', 'Colorante')], string='Tipo', readonly=True)
    color_names = fields.Char(string='Color del entintado', readonly=True)
    required_qty = fields.Float(string='Necesario pendiente', digits='Product Unit', readonly=True)
    shortage = fields.Boolean(string='Stock insuficiente', readonly=True)
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

    @api.depends_context('stock_lookup_order_line')
    @api.depends('name', 'product_id', 'product_id.name', 'product_id.default_code',
                 'tint_snapshot', 'sequence', 'order_id.order_line')
    def _compute_display_name(self):
        if not self.env.context.get('stock_lookup_order_line'):
            return super()._compute_display_name()
        for line in self:
            if line.display_type or not line.product_id:
                super(ProductStockLookupSaleLine, line)._compute_display_name()
                continue
            color = line.order_id._tint_color_description(line._tint_recipe()) if line._tint_recipe() else ''
            label = ' + '.join(filter(None, [line.product_id.display_name, color]))
            number = list(line.order_id.order_line.filtered(lambda item: not item.display_type)).index(line) + 1
            line.display_name = _('%(product)s · Línea %(line)s', product=label, line=number)

    def action_view_stock_by_warehouse(self):
        self.ensure_one()
        self.check_access('read')
        if not self.product_id:
            raise UserError(_('Selecciona primero el producto de esta línea.'))
        action = self.product_id._open_stock_lookup(company=self.company_id, order=self.order_id)
        action['context']['default_sale_line_id'] = self.id
        return action


class ProductStockLookupSaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_check_branch_stock(self):
        self.ensure_one()
        self.check_access('read')
        products = self.order_lines_for_stock_check().product_id.filtered('is_storable')
        lines = self.order_lines_for_stock_check()
        if not products:
            raise UserError(_('Agrega al menos un producto inventariable a la orden para consultar su disponibilidad.'))
        return {'type': 'ir.actions.act_window', 'name': _('Disponibilidad por sucursal'),
            'res_model': 'product.stock.lookup', 'view_mode': 'form', 'target': 'new',
            'context': {'default_sale_order_id': self.id, 'default_company_id': self.company_id.id,
                'default_product_id': lines.product_id.id if len(lines) == 1 else False,
                'default_sale_line_id': lines.id if len(lines) == 1 else False,
                'default_product_tmpl_id': False,
                'dialog_size': 'large' if any(line._tint_recipe() for line in self.order_line) else 'medium'}}
