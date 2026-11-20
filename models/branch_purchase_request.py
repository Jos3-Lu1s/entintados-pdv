import re

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError


class ResCompanyBranch(models.Model):
    _inherit = 'res.company'

    branch_supply_center = fields.Boolean(
        string='Recibe compras y surte sucursales', compute='_compute_branch_supply_center',
        store=True, readonly=False,
        help='Identifica a Principal y Veracruz en la estructura multiempresa.',
    )
    branch_requisition_allowed = fields.Boolean(
        string='Puede solicitar resurtido', compute='_compute_branch_requisition_allowed', store=True,
    )
    branch_warehouse_id = fields.Many2one(
        'stock.warehouse', string='Almacén de sucursal', check_company=True, copy=False,
        help='Almacén que se propone al solicitar o surtir materiales desde esta sucursal.',
    )

    @api.constrains('branch_warehouse_id')
    def _check_branch_warehouse(self):
        for company in self:
            if company.branch_warehouse_id and company.branch_warehouse_id.company_id != company:
                raise ValidationError(_('El almacén configurado debe pertenecer a esta sucursal.'))

    @api.depends('name')
    def _compute_branch_supply_center(self):
        for company in self:
            company.branch_supply_center = bool(re.search(
                r'\b(principal|veracruz)\b', company.name or '', re.IGNORECASE,
            ))

    @api.depends('branch_supply_center')
    def _compute_branch_requisition_allowed(self):
        for company in self:
            company.branch_requisition_allowed = not company.branch_supply_center


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    branch_requisition_allowed = fields.Boolean(
        string='Permite requisiciones de sucursal', compute='_compute_branch_requisition_allowed',
        store=True,
    )

    branch_supply_center = fields.Boolean(
        string='Recibe compras y surte sucursales',
        compute='_compute_branch_requisition_allowed', store=True,
    )

    @api.depends('company_id.branch_supply_center')
    def _compute_branch_requisition_allowed(self):
        for warehouse in self:
            warehouse.branch_supply_center = warehouse.company_id.branch_supply_center
            warehouse.branch_requisition_allowed = not warehouse.branch_supply_center


class BranchPurchaseRequest(models.Model):
    _name = 'branch.purchase.request'
    _description = 'Solicitud de resurtido entre sucursales'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _check_company_auto = True
    _order = 'id desc'

    def init(self):
        super().init()
        self.env.cr.execute("""
            UPDATE branch_purchase_request r
               SET source_company_id = w.company_id
              FROM stock_warehouse w
             WHERE r.source_warehouse_id = w.id
               AND r.source_company_id IS DISTINCT FROM w.company_id
        """)

    name = fields.Char(string='Folio', required=True, readonly=True, copy=False, default='/')
    company_id = fields.Many2one('res.company', string='Sucursal solicitante', required=True,
                                default=lambda self: self.env.company, tracking=True)
    source_company_id = fields.Many2one('res.company', string='Sucursal que surte',
                                       default=lambda self: self.env.company, tracking=True)
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Sucursal solicitante', required=True, tracking=True, check_company=True,
    )
    partner_id = fields.Many2one('res.partner', string='Proveedor')
    user_id = fields.Many2one('res.users', string='Solicitante', default=lambda self: self.env.user, readonly=True, required=True)
    line_ids = fields.One2many('branch.purchase.request.line', 'request_id', string='Productos faltantes', copy=True)
    purchase_id = fields.Many2one('purchase.order', string='Solicitud de cotización', readonly=True, copy=False,
                                  ondelete='restrict', tracking=True)
    purchase_state = fields.Selection(related='purchase_id.state', string='Estado de compra')
    expected_date = fields.Datetime(string='Fecha prevista', default=fields.Datetime.now, required=True, tracking=True)
    delivery_policy = fields.Selection([
        ('direct', 'Recibir cada producto cuando esté disponible'),
        ('one', 'Recibir todos los productos juntos'),
    ], string='Política de entrega', default='direct', required=True)
    destination_location_id = fields.Many2one(related='warehouse_id.lot_stock_id', string='Ubicación destino')
    source_location_id = fields.Many2one(related='source_warehouse_id.lot_stock_id', string='Ubicación origen')
    route_name = fields.Char(string='Ruta', compute='_compute_route_name')
    cancelled = fields.Boolean(copy=False)
    remainder_closed = fields.Boolean(copy=False, readonly=True, tracking=True)
    closure_reason = fields.Text(string='Motivo de cancelación o cierre', tracking=True, copy=False)
    requisition_ref = fields.Char(string='Requisición de compra', readonly=True, copy=False, tracking=True)
    state = fields.Selection([
        ('draft', 'Borrador'), ('sent', 'Enviado'), ('progress', 'En progreso'),
        ('done', 'Hecho'), ('cancel', 'Cancelado'),
    ], compute='_compute_transfer_progress', string='Estado', store=True, tracking=True)
    picking_ids = fields.Many2many('stock.picking', compute='_compute_transfer_progress')
    picking_count = fields.Integer(compute='_compute_transfer_progress', string='Transferencias')

    @api.depends('company_id.name', 'source_company_id.name')
    def _compute_route_name(self):
        for request in self:
            request.route_name = ' → '.join(filter(None, [
                request.source_company_id.name, request.company_id.name,
            ]))

    @api.depends('picking_id', 'picking_id.state', 'line_ids.move_ids.picking_id',
                 'line_ids.move_ids.picking_id.state', 'cancelled', 'line_ids.quantity_pending', 'remainder_closed')
    def _compute_transfer_progress(self):
        for request in self:
            pickings = request.picking_id | request.line_ids.move_ids.picking_id
            request.picking_ids = pickings
            request.picking_count = len(pickings)
            active = pickings.filtered(lambda picking: picking.state != 'cancel')
            if request.cancelled or (pickings and not active):
                request.state = 'cancel'
            elif active and all(picking.state == 'done' for picking in active) and (
                    request.remainder_closed or not any(line.quantity_pending > 0 for line in request.line_ids)):
                request.state = 'done'
            elif active.filtered(lambda picking: picking.state != 'draft'):
                request.state = 'progress'
            else:
                request.state = 'sent' if pickings else 'draft'

    def action_view_transfers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Transferencias'),
            'res_model': 'stock.picking', 'view_mode': 'list,form',
            'domain': [('id', 'in', self.picking_ids.ids)],
        }

    def action_print_request(self):
        return self.env.ref('entintados_pdv.action_report_branch_resurtido').report_action(self)

    def action_print_requisition(self):
        if any(not request.requisition_ref for request in self):
            raise UserError(_('Prepara la requisición de compra antes de imprimirla.'))
        return self.env.ref('entintados_pdv.action_report_branch_requisition').report_action(self)

    def action_send_request(self):
        self.ensure_one()
        self.action_create_transfer()
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name,
            'view_mode': 'form', 'res_id': self.id,
        }

    def action_start_transfer(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        if not self.env.su and not self.env.user.has_group('stock.group_stock_user'):
            raise UserError(_('Solo el personal de almacén puede confirmar el resurtido.'))
        self.picking_ids.filtered(lambda picking: picking.state == 'draft').action_confirm()
        return self.action_view_transfers()

    def action_cancel_request(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        if not self.closure_reason:
            raise UserError(_('Indica el motivo de cancelación en la pestaña Seguimiento.'))
        if self.picking_ids.filtered(lambda picking: picking.state == 'done'):
            raise UserError(_('Registra una devolución para los materiales que ya se trasladaron.'))
        self.picking_ids.filtered(lambda picking: picking.state != 'cancel').action_cancel()
        super().write({'cancelled': True})
        self.message_post(body=_('Solicitud cancelada por %(user)s. Motivo: %(reason)s',
                                user=self.env.user.name, reason=self.closure_reason))

    def action_close_remainder(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        (self.source_company_id or self.company_id)._check_stock_control_department()
        if self.state != 'progress' or not self.closure_reason:
            raise UserError(_('Indica el motivo para cerrar el remanente de una solicitud en progreso.'))
        self.picking_ids.filtered(lambda picking: picking.state not in ('done', 'cancel')).action_cancel()
        super().write({'remainder_closed': True})
        self.message_post(body=_('Remanente cerrado por %(user)s. Motivo: %(reason)s',
                                user=self.env.user.name, reason=self.closure_reason))

    def write(self, vals):
        if {'remainder_closed', 'cancelled'} & vals.keys():
            raise UserError(_('Utiliza Cerrar remanente para autorizar el cierre parcial.'))
        if {'warehouse_id', 'source_warehouse_id', 'company_id', 'source_company_id', 'line_ids',
                'expected_date', 'delivery_policy'} & vals.keys():
            self.env['res.company']._with_locked_records(self)
            self.invalidate_recordset(['picking_id', 'cancelled'])
            if any(request.picking_id or request.cancelled for request in self):
                raise UserError(_('Los datos de una solicitud enviada o cancelada ya no se pueden modificar.'))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['cancelled'] = False
            vals['remainder_closed'] = False
            if not vals.get('name') or vals['name'] == '/':
                company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
                vals['name'] = self.env['ir.sequence'].with_company(company).next_by_code('branch.purchase.request')
        return super().create(vals_list)

    @api.constrains('company_id', 'warehouse_id')
    def _check_warehouse_id(self):
        for request in self:
            if request.company_id.branch_supply_center:
                raise ValidationError(_('Principal y Veracruz no pueden originar requisiciones de resurtido.'))

    source_warehouse_id = fields.Many2one(
        'stock.warehouse', string='Almacén que surte', tracking=True,
    )
    picking_id = fields.Many2one(
        'stock.picking', string='Movimiento entre sucursales', readonly=True, copy=False,
        ondelete='restrict', tracking=True,
    )
    picking_state = fields.Selection(related='picking_id.state', string='Estado del movimiento')

    @api.constrains('company_id', 'warehouse_id', 'source_company_id', 'source_warehouse_id')
    def _check_transfer_warehouses(self):
        for request in self:
            if request.warehouse_id.company_id != request.company_id:
                raise ValidationError(_('La sucursal solicitante debe pertenecer a la empresa de la requisición.'))
            if request.source_warehouse_id:
                if not request.source_warehouse_id.branch_supply_center:
                    raise ValidationError(_('La sucursal que surte debe ser Principal o Veracruz.'))
                if request.source_warehouse_id.company_id != request.source_company_id:
                    raise ValidationError(_('El almacén que surte debe pertenecer a la sucursal de origen.'))
                if request.source_warehouse_id == request.warehouse_id:
                    raise ValidationError(_('Selecciona dos sucursales distintas.'))

    def action_create_transfer(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        self.invalidate_recordset(['picking_id'])
        if not self.picking_id:
            if self.cancelled:
                raise UserError(_('Esta solicitud está cancelada.'))
            self._check_warehouse_id()
            self._check_transfer_warehouses()
            if not self.source_warehouse_id or not self.line_ids:
                raise UserError(_('Selecciona la sucursal que surte y agrega los productos faltantes.'))
            if len(self.line_ids.product_id) != len(self.line_ids):
                raise UserError(_('Reúne las cantidades del mismo producto en una sola línea antes de enviar.'))
            if self.line_ids.filtered(lambda line: line.product_id.type != 'consu'):
                raise UserError(_('Las solicitudes de resurtido requieren productos físicos.'))
            source = self.source_warehouse_id.with_company(self.source_company_id)
            destination = self.warehouse_id.with_company(self.company_id)
            cross_company = self.source_company_id != self.company_id
            if not source.int_type_id or (cross_company and not source.out_type_id):
                raise UserError(_('Configura el tipo de operación interna de la sucursal que surte.'))
            destination_location = self.env.ref('stock.stock_location_customers') if cross_company else destination.lot_stock_id
            self.picking_id = self.env['stock.picking'].with_company(self.source_company_id).create({
                'company_id': self.source_company_id.id,
                'picking_type_id': (source.out_type_id if cross_company else source.int_type_id).id,
                'branch_direction': 'outbound' if cross_company else False,
                'partner_id': self.company_id.partner_id.id if cross_company else False,
                'location_id': source.lot_stock_id.id,
                'location_dest_id': destination_location.id,
                'origin': self.name,
                'scheduled_date': self.expected_date,
                'move_type': self.delivery_policy,
                'move_ids': [Command.create({
                    'branch_request_line_id': line.id,
                    'product_id': line.product_id.id,
                    'product_uom_qty': line.quantity,
                    'product_uom': line.product_id.uom_id.id,
                    'location_id': source.lot_stock_id.id,
                    'location_dest_id': destination_location.id,
                    'company_id': self.source_company_id.id,
                }) for line in self.line_ids],
            })
        return {
            'type': 'ir.actions.act_window', 'res_model': 'stock.picking',
            'view_mode': 'form', 'res_id': self.picking_id.id,
        }

    def action_create_purchase(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        self.invalidate_recordset(['purchase_id'])
        if not self.purchase_id:
            if self.cancelled or self.remainder_closed:
                raise UserError(_('La solicitud está cerrada.'))
            self._check_transfer_warehouses()
            if not self.partner_id or not self.source_warehouse_id:
                raise UserError(_('Selecciona el proveedor y la sucursal que recibe la compra en Seguimiento.'))
            if len(self.line_ids.product_id) != len(self.line_ids):
                raise UserError(_('Reúne las cantidades del mismo producto en una sola línea antes de preparar la requisición.'))
            commands = []
            for line in self.line_ids:
                own_reserved = sum(
                    move.product_uom._compute_quantity(move.quantity, line.product_uom_id)
                    for move in line.move_ids if move.state in ('assigned', 'partially_available')
                )
                shortage = max(line.quantity_pending - max(line.source_free_qty + own_reserved, 0), 0)
                if line.product_uom_id.compare(shortage, 0) > 0:
                    commands.append(Command.create({
                        'product_id': line.product_id.id, 'product_qty': shortage,
                        'product_uom_id': line.product_uom_id.id,
                    }))
            if not commands:
                raise UserError(_('Los productos solicitados tienen disponibilidad suficiente en la sucursal que surte.'))
            reference = self.env['ir.sequence'].with_company(self.source_company_id).next_by_code('branch.purchase.requisition')
            self.purchase_id = self.env['purchase.order'].with_company(self.source_company_id).create({
                'company_id': self.source_company_id.id, 'partner_id': self.partner_id.id,
                'picking_type_id': self.source_warehouse_id.in_type_id.id,
                'origin': '%s / %s' % (reference, self.name),
                'branch_request_id': self.id, 'order_line': commands,
            })
            self.requisition_ref = reference
            self.message_post(body=_('Requisición %(reference)s vinculada a %(purchase)s.',
                                    reference=reference, purchase=self.purchase_id.name))
        return {
            'type': 'ir.actions.act_window', 'res_model': 'purchase.order',
            'view_mode': 'form', 'res_id': self.purchase_id.id,
        }

    @api.onchange('company_id', 'source_company_id')
    def _onchange_default_supply_center(self):
        if not self.source_company_id or not self.source_company_id.branch_supply_center:
            self.source_company_id = self.env.companies.filtered('branch_supply_center')[:1]
        if self.source_company_id and self.source_warehouse_id.company_id != self.source_company_id:
            self.source_warehouse_id = self.source_company_id.branch_warehouse_id or (
                self.env['stock.warehouse'].with_company(self.source_company_id).search([
                    ('company_id', '=', self.source_company_id.id),
                ], limit=1))
        if self.company_id and self.warehouse_id.company_id != self.company_id:
            self.warehouse_id = self.company_id.branch_warehouse_id or (
                self.env['stock.warehouse'].with_company(self.company_id).search([
                    ('company_id', '=', self.company_id.id),
                ], limit=1))


class BranchPurchaseRequestLine(models.Model):
    _name = 'branch.purchase.request.line'
    _description = 'Producto faltante de una solicitud de resurtido'
    _check_company_auto = True

    request_id = fields.Many2one('branch.purchase.request', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='request_id.company_id', store=True)
    product_id = fields.Many2one('product.product', string='Producto', required=True, check_company=True)
    quantity = fields.Float(string='Cantidad', required=True, default=1, digits='Product Unit')
    product_uom_id = fields.Many2one(related='product_id.uom_id', string='Unidad de medida')
    route_name = fields.Char(related='request_id.route_name', string='Ruta')
    move_ids = fields.One2many('stock.move', 'branch_request_line_id')
    quantity_progress = fields.Float(string='Cantidad en progreso', compute='_compute_quantities', digits='Product Unit')
    quantity_reserved = fields.Float(string='Cantidad reservada', compute='_compute_quantities', digits='Product Unit')
    quantity_done = fields.Float(string='Cantidad finalizada', compute='_compute_quantities', digits='Product Unit')
    quantity_pending = fields.Float(string='Cantidad pendiente', compute='_compute_quantities',
                                    digits='Product Unit', store=True)
    source_physical_qty = fields.Float(string='Existencia física origen', compute='_compute_source_stock', digits='Product Unit')
    source_free_qty = fields.Float(string='Disponible origen', compute='_compute_source_stock', digits='Product Unit')
    source_reserved_qty = fields.Float(string='Reservado origen', compute='_compute_source_stock', digits='Product Unit')
    source_forecast_qty = fields.Float(string='Previsto origen', compute='_compute_source_stock', digits='Product Unit')
    state = fields.Selection(related='request_id.state', string='Estado')

    @api.depends('move_ids.state', 'move_ids.quantity', 'move_ids.product_uom_qty',
                 'move_ids.product_uom', 'product_uom_id', 'quantity', 'move_ids.picking_id.branch_direction')
    def _compute_quantities(self):
        for line in self:
            line.quantity_progress = sum(
                move.product_uom._compute_quantity(move.product_uom_qty, line.product_uom_id)
                for move in line.move_ids if move.state not in ('done', 'cancel')
            )
            line.quantity_done = sum(
                move.product_uom._compute_quantity(move.quantity, line.product_uom_id)
                for move in line.move_ids if move.state == 'done' and move.picking_id.branch_direction != 'outbound'
            )
            line.quantity_pending = max(line.quantity - line.quantity_done, 0)
            line.quantity_reserved = sum(
                move.product_uom._compute_quantity(move.quantity, line.product_uom_id)
                for move in line.move_ids if move.state in ('assigned', 'partially_available')
            )

    @api.depends('product_id', 'product_id.qty_available', 'product_id.free_qty',
                 'product_id.virtual_available', 'request_id.source_warehouse_id', 'product_uom_id',
                 'request_id.source_company_id')
    def _compute_source_stock(self):
        for line in self:
            product = line.product_id.with_company(line.request_id.source_company_id or self.env.company).with_context(
                warehouse_id=line.request_id.source_warehouse_id.id)
            valid = bool(product and line.product_uom_id and line.request_id.source_warehouse_id)
            line.source_physical_qty = product.qty_available if valid else 0
            line.source_free_qty = product.free_qty if valid else 0
            line.source_reserved_qty = max(line.source_physical_qty - line.source_free_qty, 0)
            line.source_forecast_qty = product.virtual_available if valid else 0

    @api.model_create_multi
    def create(self, vals_list):
        requests = self.env['branch.purchase.request'].browse(
            [vals['request_id'] for vals in vals_list if vals.get('request_id')]
        )
        if any(request.picking_id or request.cancelled for request in requests):
            raise UserError(_('Agrega productos antes de enviar la solicitud.'))
        return super().create(vals_list)

    def write(self, vals):
        if {'product_id', 'quantity', 'request_id'} & vals.keys() and any(
            line.request_id.picking_id or line.request_id.cancelled for line in self
        ):
            raise UserError(_('Los productos de una solicitud enviada ya no se pueden modificar.'))
        return super().write(vals)

    def unlink(self):
        if any(line.request_id.picking_id or line.request_id.cancelled for line in self):
            raise UserError(_('Los productos de una solicitud enviada ya no se pueden eliminar.'))
        return super().unlink()

    _positive_quantity = models.Constraint('CHECK(quantity > 0)', 'La cantidad debe ser mayor que cero.')


class StockMoveBranchRequest(models.Model):
    _inherit = 'stock.move'

    branch_request_line_id = fields.Many2one(
        'branch.purchase.request.line', string='Línea de solicitud de resurtido',
        copy=True, index=True, ondelete='restrict',
    )

    def copy_data(self, default=None):
        values = super().copy_data(default=default)
        if default and default.get('origin_returned_move_id'):
            for vals in values:
                vals['branch_request_line_id'] = False
        return values

    @api.constrains('branch_request_line_id', 'company_id', 'product_id')
    def _check_branch_request_link(self):
        for move in self.filtered('branch_request_line_id'):
            line = move.branch_request_line_id
            request = line.request_id
            if move.product_id != line.product_id or move.company_id not in (request.company_id | request.source_company_id):
                raise ValidationError(_('El movimiento debe corresponder al producto y a una sucursal de la solicitud SRO.'))


class PurchaseOrderBranchReceipt(models.Model):
    _inherit = 'purchase.order'
    branch_request_id = fields.Many2one(
        'branch.purchase.request', string='Solicitud de resurtido',
        readonly=True, copy=False, ondelete='restrict', index=True,
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'picking_type_id' in fields_list and not self.env.context.get('default_picking_type_id'):
            company = self.env['res.company'].browse(
                values.get('company_id') or self.env.context.get('default_company_id') or self.env.company.id)
            if company.branch_warehouse_id.in_type_id:
                values['picking_type_id'] = company.branch_warehouse_id.in_type_id.id
        return values

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('company_id') and not vals.get('picking_type_id'):
                company = self.env['res.company'].browse(vals['company_id'])
                if company.branch_warehouse_id.in_type_id:
                    vals['picking_type_id'] = company.branch_warehouse_id.in_type_id.id
        return super().create(vals_list)

    def _check_branch_purchase_receipt(self):
        for order in self:
            warehouse = order.picking_type_id.warehouse_id
            if not warehouse or not warehouse.branch_supply_center:
                raise ValidationError(_('Las compras deben recibirse en Principal o Veracruz. Selecciona su operación de recepción en Entregar a.'))

    def button_confirm(self):
        self._check_branch_purchase_receipt()
        return super().button_confirm()

    def button_approve(self, force=False):
        self._check_branch_purchase_receipt()
        return super().button_approve(force=force)


class StockPickingBranchReceipt(models.Model):
    _inherit = 'stock.picking'

    branch_direction = fields.Selection([
        ('outbound', 'Salida entre sucursales'), ('inbound', 'Entrada entre sucursales'),
    ], string='Movimiento entre sucursales', compute='_compute_branch_direction', store=True, copy=False, index=True)
    branch_source_picking_id = fields.Many2one(
        'stock.picking', string='Salida de origen', readonly=True, copy=False, ondelete='restrict',
    )
    _unique_branch_receipt = models.Constraint(
        'UNIQUE(branch_source_picking_id)', 'Esta salida ya tiene una entrada vinculada.',
    )

    def action_view_branch_source(self):
        self.ensure_one()
        if self.branch_source_picking_id:
            return {
                'type': 'ir.actions.act_window', 'res_model': 'stock.picking', 'view_mode': 'form',
                'res_id': self.branch_source_picking_id.id,
            }

    @api.depends('move_ids.branch_request_line_id.request_id.company_id',
                 'move_ids.branch_request_line_id.request_id.source_company_id',
                 'company_id', 'location_id', 'location_dest_id')
    def _compute_branch_direction(self):
        for picking in self:
            request = picking.move_ids.branch_request_line_id.request_id
            picking.branch_direction = False
            if len(request) != 1 or request.company_id == request.source_company_id:
                continue
            if picking.company_id == request.source_company_id and picking.location_dest_id.usage == 'customer':
                picking.branch_direction = 'outbound'
            elif picking.company_id == request.company_id and picking.location_id.usage == 'supplier':
                picking.branch_direction = 'inbound'

    def _receive_interbranch_transfer(self):
        self.ensure_one()
        if self.branch_direction != 'outbound' or self.state != 'done':
            return
        lock = self.env['res.company']._with_locked_records
        lock(self)
        request = self.move_ids.branch_request_line_id.request_id
        if len(request) != 1:
            raise ValidationError(_('La salida entre sucursales debe corresponder a una sola solicitud SRO.'))
        destination_company = request.company_id
        Receipt = self.env['stock.picking'].with_company(destination_company)
        receipt = Receipt.search([('branch_source_picking_id', '=', self.id)], limit=1)
        if receipt:
            if receipt.state != 'done':
                raise UserError(_('La entrada vinculada está pendiente. Revisa el movimiento antes de reintentar.'))
            return receipt
        warehouse = request.warehouse_id.with_company(destination_company)
        if not warehouse.in_type_id:
            raise UserError(_('Configura la operación de recepción de la sucursal destino.'))
        supplier_location = self.env.ref('stock.stock_location_suppliers')
        commands = []
        for move in self.move_ids.filtered(lambda item: item.state == 'done' and item.quantity > 0):
            detail_commands = []
            for detail in move.move_line_ids.filtered(lambda item: item.quantity > 0):
                lot = detail.lot_id
                if lot and lot.company_id and lot.company_id != destination_company:
                    Lot = self.env['stock.lot'].with_company(destination_company)
                    lot = Lot.search([
                        ('name', '=', detail.lot_id.name), ('product_id', '=', detail.product_id.id),
                        ('company_id', 'in', [False, destination_company.id]),
                    ], limit=1) or Lot.create({
                        'name': detail.lot_id.name, 'product_id': detail.product_id.id,
                        'company_id': destination_company.id,
                    })
                detail_commands.append(Command.create({
                    'company_id': destination_company.id, 'product_id': detail.product_id.id,
                    'product_uom_id': detail.product_uom_id.id, 'quantity': detail.quantity,
                    'location_id': supplier_location.id, 'location_dest_id': warehouse.lot_stock_id.id,
                    'lot_id': lot.id, 'owner_id': detail.owner_id.id,
                }))
            commands.append(Command.create({
                'company_id': destination_company.id, 'product_id': move.product_id.id,
                'product_uom': move.product_uom.id, 'product_uom_qty': move.quantity,
                'location_id': supplier_location.id, 'location_dest_id': warehouse.lot_stock_id.id,
                'branch_request_line_id': move.branch_request_line_id.id,
                'move_line_ids': detail_commands,
            }))
        if not commands:
            return
        receipt = Receipt.create({
            'company_id': destination_company.id, 'picking_type_id': warehouse.in_type_id.id,
            'branch_direction': 'inbound', 'branch_source_picking_id': self.id,
            'partner_id': self.company_id.partner_id.id, 'origin': request.name,
            'location_id': supplier_location.id, 'location_dest_id': warehouse.lot_stock_id.id,
            'move_ids': commands,
        })
        receipt.move_ids.move_line_ids.write({'picking_id': receipt.id})
        receipt.action_confirm()
        receipt.move_ids.picked = True
        receipt._action_done()
        request.message_post(body=_('Salida %(outgoing)s de %(source)s recibida en %(destination)s con entrada %(incoming)s.',
                                    outgoing=self.name, source=self.company_id.name,
                                    destination=destination_company.name, incoming=receipt.name))
        return receipt

    def _action_done(self):
        for picking in self:
            if picking.picking_type_code == 'incoming' and picking.move_ids.purchase_line_id:
                warehouse = picking.location_dest_id.warehouse_id
                if not warehouse or not warehouse.branch_supply_center or warehouse.company_id != picking.company_id:
                    raise ValidationError(_('Los materiales de compras solo pueden recibirse en Principal o Veracruz de la misma empresa.'))
        return super()._action_done()
