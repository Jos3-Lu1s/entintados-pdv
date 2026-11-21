import hashlib
import json
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

CONTROLLED_PICKING_FIELDS = {
    'transfer_approved_by', 'transfer_approved_at', 'transfer_approval_token',
    'physical_review_by', 'physical_review_at', 'physical_review_token',
    'clarification_state', 'clarification_deadline',
    'collected_by', 'collected_at',
}


class ResCompany(models.Model):
    _inherit = 'res.company'

    stock_control_department_id = fields.Many2one(
        'hr.department', string='Departamento de autorizaciones y revisión física',
        default=lambda self: self.env.ref('entintados_pdv.department_stock_control', raise_if_not_found=False),
    )
    stock_clarification_days = fields.Integer(string='Días para aclaraciones', default=0)
    stock_require_signature = fields.Boolean(string='Exigir firma en autorización de traslado', default=True)
    stock_require_receipt_evidence = fields.Boolean(string='Exigir evidencia en revisión física', default=True)
    purchase_receipt_difference_policy = fields.Selection([
        ('warn', 'Mostrar diferencias'), ('block', 'Bloquear facturación superior a lo recibido'),
    ], string='Diferencias entre recepción y factura', default='warn', required=True)
    sale_margin_control = fields.Boolean(string='Controlar margen mínimo en ventas')
    sale_minimum_margin = fields.Float(string='Margen mínimo de venta', default=0)
    _valid_sale_minimum_margin = models.Constraint(
        'CHECK(sale_minimum_margin >= 0 AND sale_minimum_margin <= 1)',
        'El margen mínimo debe estar entre 0% y 100%.',
    )
    _nonnegative_clarification_days = models.Constraint(
        'CHECK(stock_clarification_days >= 0)', 'Los días para aclaraciones no pueden ser negativos.',
    )

    def _stock_control_department(self):
        self.ensure_one()
        return self.stock_control_department_id or self.env.ref(
            'entintados_pdv.department_stock_control', raise_if_not_found=False)

    @api.model
    def _configure_stock_control_department(self):
        department = self.env.ref('entintados_pdv.department_stock_control')
        self.search([('stock_control_department_id', '=', False)]).write({
            'stock_control_department_id': department.id,
        })

    def _can_control_stock(self):
        self.ensure_one()
        if self.env.su:
            return True
        department = self._stock_control_department()
        return bool(department and self.env['hr.employee'].sudo().search_count([
            ('user_id', '=', self.env.uid), ('company_id', '=', self.id),
            ('department_id', 'child_of', department.id), ('active', '=', True),
        ]))

    def _check_stock_control_department(self):
        for company in self:
            if not company._can_control_stock():
                raise UserError(_('Esta acción corresponde al departamento de autorizaciones y revisión física de almacén.'))


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    stock_control_department_id = fields.Many2one(related='company_id.stock_control_department_id', readonly=False)
    branch_supply_center = fields.Boolean(related='company_id.branch_supply_center', readonly=False)
    stock_clarification_days = fields.Integer(related='company_id.stock_clarification_days', readonly=False)
    stock_require_signature = fields.Boolean(related='company_id.stock_require_signature', readonly=False)
    stock_require_receipt_evidence = fields.Boolean(related='company_id.stock_require_receipt_evidence', readonly=False)
    purchase_receipt_difference_policy = fields.Selection(
        related='company_id.purchase_receipt_difference_policy', readonly=False,
    )
    sale_margin_control = fields.Boolean(related='company_id.sale_margin_control', readonly=False)
    sale_minimum_margin = fields.Float(related='company_id.sale_minimum_margin', readonly=False)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    purchase_line_product = fields.Boolean(
        string='Producto de línea para compras', default=True,
        help='Catálogo autorizado para compras. Es independiente de las líneas y fórmulas de entintado.',
        tracking=True,
    )


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    can_control_stock = fields.Boolean(compute='_compute_can_control_stock')
    purchase_receipt = fields.Boolean(compute='_compute_purchase_receipt')
    branch_request_id = fields.Many2one(
        'branch.purchase.request', compute='_compute_branch_request_id',
        string='Solicitud de resurtido', store=True, index=True,
    )
    control_signature = fields.Binary(string='Firma de autorización', attachment=True, copy=False)
    transfer_approved_by = fields.Many2one('res.users', string='Traslado autorizado por', readonly=True, copy=False)
    transfer_approved_at = fields.Datetime(string='Fecha de autorización', readonly=True, copy=False)
    transfer_approval_token = fields.Char(readonly=True, copy=False)
    transfer_authorization_state = fields.Selection([
        ('none', 'No aplica'), ('pending', 'Esperando aprobación'), ('approved', 'Autorizado'),
    ], string='Autorización de traslado', compute='_compute_transfer_authorization_state', store=True, index=True)
    collected_by = fields.Many2one('res.users', string='Recolectado por', readonly=True, copy=False)
    collected_at = fields.Datetime(string='Fecha de recolección', readonly=True, copy=False)
    physical_review_by = fields.Many2one('res.users', string='Revisión física por', readonly=True, copy=False)
    physical_review_at = fields.Datetime(string='Fecha de revisión física', readonly=True, copy=False)
    physical_review_token = fields.Char(readonly=True, copy=False)
    physical_review_notes = fields.Text(string='Observaciones de revisión física', copy=False, tracking=True)
    receipt_comparison = fields.Text(string='Comparación contra pedido y factura', compute='_compute_receipt_comparison')
    clarification_days = fields.Integer(
        string='Días para aclaración', default=0,
        tracking=True,
    )
    clarification_deadline = fields.Date(string='Fecha límite de aclaración', readonly=True, copy=False, tracking=True)
    clarification_responsible_id = fields.Many2one(
        'res.users', string='Responsable de aclaración', default=lambda self: self.env.user,
        domain=[('share', '=', False)], tracking=True,
    )
    clarification_notes = fields.Text(string='Diferencia o aclaración', tracking=True, copy=False)
    clarification_resolution = fields.Text(string='Resolución', tracking=True, copy=False)
    clarification_state = fields.Selection([
        ('none', 'Sin aclaración'), ('open', 'Abierta'), ('closed', 'Cerrada'),
    ], string='Aclaración', default='none', readonly=True, copy=False, tracking=True)
    _positive_clarification_days = models.Constraint(
        'CHECK(clarification_days >= 0)', 'Los días para aclaración no pueden ser negativos.',
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'clarification_days' in fields_list:
            company = self.env['res.company'].browse(
                values.get('company_id') or self.env.context.get('default_company_id') or self.env.company.id)
            values['clarification_days'] = company.stock_clarification_days
        return values

    @api.depends_context('uid')
    @api.depends('company_id', 'company_id.stock_control_department_id')
    def _compute_can_control_stock(self):
        for picking in self:
            picking.can_control_stock = picking.company_id._can_control_stock()

    @api.depends('picking_type_code', 'move_ids.purchase_line_id')
    def _compute_purchase_receipt(self):
        for picking in self:
            picking.purchase_receipt = bool(picking.picking_type_code == 'incoming' and picking.move_ids.purchase_line_id)

    @api.depends('picking_type_code', 'branch_direction', 'transfer_approved_by', 'transfer_approval_token',
                 'move_ids.product_id', 'move_ids.product_uom_qty', 'move_ids.product_uom',
                 'location_id', 'location_dest_id', 'control_signature', 'state',
                 'evidence_attachment_ids', 'evidence_attachment_ids.checksum')
    def _compute_transfer_authorization_state(self):
        for picking in self:
            if picking.picking_type_code != 'internal' and picking.branch_direction != 'outbound':
                picking.transfer_authorization_state = 'none'
            elif picking.transfer_approved_by and (
                    picking.state == 'done' or picking.transfer_approval_token == picking._control_token()):
                picking.transfer_authorization_state = 'approved'
            else:
                picking.transfer_authorization_state = 'pending'

    @api.depends('move_ids.branch_request_line_id.request_id')
    def _compute_branch_request_id(self):
        for picking in self:
            picking.branch_request_id = picking.move_ids.branch_request_line_id.request_id[:1]

    def action_view_branch_request(self):
        self.ensure_one()
        if self.branch_request_id:
            return {
                'type': 'ir.actions.act_window', 'res_model': 'branch.purchase.request',
                'view_mode': 'form', 'res_id': self.branch_request_id.id,
            }

    @api.depends('move_ids.quantity', 'move_ids.product_uom_qty', 'move_ids.purchase_line_id.qty_invoiced',
                 'move_ids.purchase_line_id.qty_received', 'move_ids.product_uom')
    def _compute_receipt_comparison(self):
        for picking in self:
            rows = []
            for move in picking.move_ids.filtered('purchase_line_id'):
                line = move.purchase_line_id
                rows.append(_('%(product)s · Pedido: %(ordered)s · Recibido acumulado: %(received)s · '
                              'Facturado (con borradores): %(invoiced)s · En esta recepción: %(current)s %(unit)s',
                              product=move.product_id.display_name, ordered=line.product_qty,
                              received=line.qty_received, invoiced=line.qty_invoiced,
                              current=move.product_uom._compute_quantity(move.quantity, line.product_uom_id),
                              unit=line.product_uom_id.name))
            for move in picking.move_ids.filtered(lambda item: not item.purchase_line_id):
                if picking.purchase_receipt:
                    rows.append(_('Producto sin línea de compra: %s', move.product_id.display_name))
            picking.receipt_comparison = '\n'.join(rows)

    def _control_token(self, physical=False):
        self.ensure_one()
        values = [self.company_id.id, self.location_id.id, self.location_dest_id.id, [
            [move.id, move.product_id.id, move.product_uom.id, move.product_uom_qty,
             [move.quantity, move.purchase_line_id.id] if physical else None]
            for move in self.move_ids.sorted('id') if move.state != 'cancel'
        ]]
        values.append([[attachment.id, attachment.checksum]
                       for attachment in self.evidence_attachment_ids.sorted('id')])
        if physical:
            values.append(self.physical_review_notes or '')
        else:
            signature = self.with_context(bin_size=False).control_signature or b''
            values.append(hashlib.sha256(signature if isinstance(signature, bytes) else signature.encode()).hexdigest())
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def action_approve_internal_transfer(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        self.company_id._check_stock_control_department()
        if (self.picking_type_code != 'internal' and self.branch_direction != 'outbound') or self.state in ('done', 'cancel'):
            raise UserError(_('Autoriza un traslado interno pendiente.'))
        if self.company_id.stock_require_signature and not self.control_signature:
            raise UserError(_('Registra la firma de autorización en Control de almacén.'))
        super().write({
            'transfer_approved_by': self.env.uid, 'transfer_approved_at': fields.Datetime.now(),
            'transfer_approval_token': self._control_token(),
        })
        self.message_post(body=_('Traslado autorizado por %s.', self.env.user.name))

    def action_review_purchase_receipt(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        self.company_id._check_stock_control_department()
        if not self.purchase_receipt or self.state in ('done', 'cancel'):
            raise UserError(_('La revisión física corresponde a una recepción de compra pendiente.'))
        if self.company_id.stock_require_receipt_evidence and not self.evidence_attachment_ids:
            raise UserError(_('Adjunta las evidencias de la revisión física en Control de almacén.'))
        differences = self.move_ids.filtered(
            lambda move: not move.purchase_line_id or move.product_uom.compare(move.quantity, move.product_uom_qty) != 0)
        if differences and not self.physical_review_notes:
            raise UserError(_('Registra las diferencias de cantidades o productos en las observaciones de revisión física.'))
        super().write({
            'physical_review_by': self.env.uid, 'physical_review_at': fields.Datetime.now(),
            'physical_review_token': self._control_token(physical=True),
        })
        self.message_post(body=_('Revisión física registrada por %s.', self.env.user.name))

    def action_record_collection(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        if not self.env.su and not self.env.user.has_group('stock.group_stock_user'):
            raise UserError(_('Solo el personal de almacén puede registrar la recolección.'))
        if (self.picking_type_code != 'internal' and self.branch_direction != 'outbound') or self.state in ('done', 'cancel'):
            raise UserError(_('Selecciona un traslado interno pendiente.'))
        self._check_operation_authorizations()
        super().write({'collected_by': self.env.uid, 'collected_at': fields.Datetime.now()})
        self.message_post(body=_('Material recolectado por %s.', self.env.user.name))

    def _check_operation_authorizations(self):
        self.env['res.company']._with_locked_records(self.sorted('id'))
        moves = self.move_ids.sorted('id')
        if moves:
            self.env['res.company']._with_locked_records(moves)
            moves.invalidate_recordset(['product_id', 'product_uom', 'product_uom_qty', 'quantity', 'state', 'purchase_line_id'])
        self.invalidate_recordset([
            'location_id', 'location_dest_id', 'transfer_approved_by', 'transfer_approval_token',
            'physical_review_by', 'physical_review_token', 'physical_review_notes', 'control_signature',
            'evidence_attachment_ids',
        ])
        for picking in self.filtered(lambda record: record.state not in ('done', 'cancel')):
            request = picking.branch_request_id
            if request:
                if picking.branch_direction == 'outbound':
                    route_valid = (picking.company_id == request.source_company_id
                                   and picking.location_id.warehouse_id == request.source_warehouse_id
                                   and request.source_company_id.branch_supply_center
                                   and not request.company_id.branch_supply_center
                                   and all(detail.location_id.warehouse_id == request.source_warehouse_id
                                           and detail.location_dest_id.usage == 'customer'
                                           for detail in picking.move_line_ids if detail.quantity > 0))
                elif picking.branch_direction == 'inbound':
                    route_valid = (picking.company_id == request.company_id
                                   and picking.location_dest_id.warehouse_id == request.warehouse_id
                                   and all(detail.location_id.usage == 'supplier'
                                           and detail.location_dest_id.warehouse_id == request.warehouse_id
                                           for detail in picking.move_line_ids if detail.quantity > 0))
                else:
                    route_valid = (picking.location_id.warehouse_id == request.source_warehouse_id
                                   and picking.location_dest_id.warehouse_id == request.warehouse_id)
                if not route_valid:
                    raise UserError(_('El traslado debe respetar las sucursales de origen y destino de la solicitud SRO.'))
            if (picking.picking_type_code == 'internal' or picking.branch_direction == 'outbound') and (
                    not picking.transfer_approved_by or picking.transfer_approval_token != picking._control_token()
                    or (picking.company_id.stock_require_signature and not picking.control_signature)):
                raise UserError(_('Autoriza el traslado en Control de almacén antes de validarlo. '
                                  'Los cambios en productos, cantidades o ubicaciones requieren renovar la autorización.'))
            if request and any(move.product_uom.compare(move.quantity, move.product_uom_qty) > 0
                               for move in picking.move_ids if move.state != 'cancel'):
                raise UserError(_('La cantidad realizada supera la autorizada. Ajusta la demanda y renueva la autorización.'))
            if picking.purchase_receipt and (
                    not picking.physical_review_by or picking.physical_review_token != picking._control_token(physical=True)
                    or (picking.company_id.stock_require_receipt_evidence and not picking.evidence_attachment_ids)):
                raise UserError(_('Registra la revisión física de las cantidades y evidencias actuales antes de validar la recepción.'))

    def button_validate(self):
        self._check_operation_authorizations()
        return super().button_validate()

    def action_confirm(self):
        if self.filtered('branch_request_id') and not self.env.su and not self.env.user.has_group('stock.group_stock_user'):
            raise UserError(_('Solo el personal de almacén puede confirmar el movimiento de una solicitud SRO.'))
        return super().action_confirm()

    def _action_done(self):
        with self.env.cr.savepoint():
            self._check_operation_authorizations()
            for picking in self.filtered(lambda record: record.branch_request_id.delivery_policy == 'one'):
                if any(move.product_uom.compare(move.quantity, move.product_uom_qty) < 0
                       for move in picking.move_ids if move.state != 'cancel'):
                    raise UserError(_('La solicitud requiere recibir todos los productos juntos. Completa las cantidades antes de validar.'))
            result = super()._action_done()
            for picking in self.filtered(lambda record: record.branch_direction == 'outbound' and record.state == 'done'):
                picking._receive_interbranch_transfer()
            return result

    def action_open_clarification(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        if self.clarification_state == 'open':
            raise UserError(_('La aclaración ya está abierta y conserva su fecha límite.'))
        if not self.clarification_notes or not self.clarification_responsible_id:
            raise UserError(_('Indica la diferencia y su responsable en Aclaraciones.'))
        try:
            deadline = fields.Date.context_today(self) + timedelta(days=self.clarification_days)
        except OverflowError:
            raise ValidationError(_('El plazo excede las fechas admitidas. Revisa los días para aclaración.')) from None
        super().write({'clarification_state': 'open', 'clarification_deadline': deadline})
        activity = self.activity_ids.filtered(lambda item: item.summary == _('Aclaración de almacén'))
        values = {'user_id': self.clarification_responsible_id.id, 'date_deadline': deadline}
        if activity:
            activity.write(values)
        else:
            self.activity_schedule('mail.mail_activity_data_todo', summary=_('Aclaración de almacén'),
                                   note=self.clarification_notes, **values)
        self.message_post(body=_('Aclaración abierta. Responsable: %(user)s. Fecha límite: %(date)s.',
                                user=self.clarification_responsible_id.name, date=deadline))

    def action_close_clarification(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        self.company_id._check_stock_control_department()
        if self.clarification_state != 'open' or not self.clarification_resolution:
            raise UserError(_('Registra la resolución de la aclaración abierta.'))
        super().write({'clarification_state': 'closed'})
        self.activity_ids.filtered(lambda item: item.summary == _('Aclaración de almacén')).action_feedback(
            feedback=self.clarification_resolution)
        self.message_post(body=_('Aclaración resuelta por %(user)s: %(resolution)s',
                                user=self.env.user.name, resolution=self.clarification_resolution))

    def write(self, vals):
        if CONTROLLED_PICKING_FIELDS & vals.keys():
            raise UserError(_('Usa los botones de Control de almacén para registrar autorizaciones y aclaraciones.'))
        if 'clarification_days' in vals and self.filtered(lambda picking: picking.clarification_state == 'open'):
            raise UserError(_('Los días de una aclaración abierta conservan el plazo registrado. Cierra y abre una nueva aclaración si procede.'))
        if {'location_id', 'location_dest_id', 'control_signature', 'physical_review_notes',
                'evidence_attachment_ids', 'move_ids'} & vals.keys():
            self.check_access('write')
            self.env['res.company']._with_locked_records(self.sorted('id'))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if any(vals.get(name) for name in CONTROLLED_PICKING_FIELDS - {'clarification_state'}):
                raise UserError(_('Las autorizaciones se registran después de crear el movimiento.'))
            if vals.get('clarification_state', 'none') != 'none':
                raise UserError(_('Utiliza Abrir aclaración después de crear el movimiento.'))
            if 'clarification_days' not in vals:
                company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
                vals['clarification_days'] = company.stock_clarification_days
        return super().create(vals_list)


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    def _action_done(self):
        if any(line.picking_id.picking_type_code == 'internal' and line.quantity > 0
               and line.location_id == line.location_dest_id for line in self):
            raise ValidationError(_('La ubicación de origen y destino debe ser distinta en las cantidades realizadas del traslado.'))
        return super()._action_done()


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def button_confirm(self):
        for order in self:
            if not order.partner_id or not order.order_line.filtered(lambda line: not line.display_type):
                raise ValidationError(_('Indica proveedor y productos antes de confirmar la compra.'))
            for line in order.order_line.filtered(lambda row: not row.display_type):
                if not line.product_id.purchase_line_product:
                    raise ValidationError(_('El producto %s está fuera del catálogo de línea para compras.',
                                            line.product_id.display_name))
                if line.product_qty <= 0 or line.price_unit < 0 or not line.product_uom_id:
                    raise ValidationError(_('Revisa cantidad, precio y unidad de %s.', line.product_id.display_name))
        return super().button_confirm()

    def write(self, vals):
        if {'partner_id', 'currency_id', 'picking_type_id', 'payment_term_id'} & vals.keys() and self.filtered(
                lambda order: order.state == 'purchase') and not self.env.su and not self.env.user.has_group(
                    'purchase.group_purchase_manager'):
            raise UserError(_('Solo un responsable de Compras puede modificar las condiciones de una compra confirmada.'))
        return super().write(vals)


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def write(self, vals):
        if {'product_id', 'product_qty', 'product_uom_id', 'price_unit', 'discount', 'taxes_id', 'tax_ids'} & vals.keys():
            if self.filtered(lambda line: line.order_id.state == 'purchase') and not self.env.su and not self.env.user.has_group(
                    'purchase.group_purchase_manager'):
                raise UserError(_('Solo un responsable de Compras puede modificar productos, cantidades o precios de una compra confirmada.'))
        return super().write(vals)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    contact_origin = fields.Selection([
        ('chat', 'Chat'), ('phone', 'Llamada'), ('email', 'Correo'), ('store', 'Sucursal'), ('other', 'Otro'),
    ], string='Origen del contacto', tracking=True)

    def action_confirm(self):
        if not self.env.su and not self.env.user.has_group('sales_team.group_sale_manager'):
            for order in self:
                if not order.company_id.sale_margin_control:
                    continue
                lines = order.sudo().order_line.filtered(
                    lambda line: not line.display_type and not line.is_downpayment
                    and line.product_id.is_storable and not line.tint_snapshot)
                if any(line.osel_margin_ratio < order.company_id.sale_minimum_margin for line in lines):
                    raise UserError(_('Esta cotización requiere confirmación de un responsable de Ventas por su margen.'))
        return super().action_confirm()


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    availability_warehouse_id = fields.Many2one(related='order_id.warehouse_id', string='Almacén consultado')
    osel_cost_amount = fields.Monetary(
        string='Costo de línea', compute='_compute_osel_margin', compute_sudo=True,
        groups='sales_team.group_sale_manager',
    )
    osel_margin_amount = fields.Monetary(
        string='Margen de línea', compute='_compute_osel_margin', compute_sudo=True,
        groups='sales_team.group_sale_manager',
    )
    osel_margin_ratio = fields.Float(
        string='Margen de línea (%)', compute='_compute_osel_margin', compute_sudo=True,
        groups='sales_team.group_sale_manager',
    )

    @api.depends(lambda self: [
        'price_subtotal', 'product_uom_qty', 'product_id.standard_price', 'product_uom_id',
        'order_id.currency_id', 'order_id.company_id', 'order_id.date_order',
    ] + (['purchase_price'] if 'purchase_price' in self._fields else []))
    def _compute_osel_margin(self):
        for line in self:
            product = line.product_id.with_company(line.company_id or self.env.company)
            unit_cost = 0
            if product and line.product_uom_id:
                if 'purchase_price' in line._fields:
                    unit_cost = line.purchase_price
                else:
                    cost = product.uom_id._compute_price(product.standard_price, line.product_uom_id)
                    unit_cost = line.company_id.currency_id._convert(
                        cost, line.currency_id, line.company_id,
                        (line.order_id.date_order or fields.Datetime.now()).date(),
                    )
            line.osel_cost_amount = unit_cost * line.product_uom_qty
            line.osel_margin_amount = line.price_subtotal - line.osel_cost_amount
            line.osel_margin_ratio = line.osel_margin_amount / line.price_subtotal if line.price_subtotal else (
                -1 if line.osel_cost_amount else 0)


class AccountMove(models.Model):
    _inherit = 'account.move'

    purchase_receipt_comparison = fields.Text(string='Comparación compra / recepción / factura',
                                               compute='_compute_purchase_receipt_comparison')
    purchase_receipt_has_difference = fields.Boolean(compute='_compute_purchase_receipt_comparison')

    @api.depends('invoice_line_ids.quantity', 'invoice_line_ids.purchase_line_id.qty_received',
                 'invoice_line_ids.purchase_line_id.product_qty', 'invoice_line_ids.product_uom_id',
                 'invoice_line_ids.purchase_line_id.qty_invoiced')
    def _compute_purchase_receipt_comparison(self):
        for invoice in self:
            rows = []
            difference = False
            for line in invoice.invoice_line_ids.filtered('purchase_line_id'):
                purchase = line.purchase_line_id
                quantity = line.product_uom_id._compute_quantity(line.quantity, purchase.product_uom_id)
                rows.append(_('%(product)s · %(order)s · Pedido: %(ordered)s · Recibido: %(received)s · '
                              'Esta factura: %(invoiced)s %(unit)s',
                              product=line.product_id.display_name, order=purchase.order_id.name,
                              ordered=purchase.product_qty, received=purchase.qty_received,
                              invoiced=quantity, unit=purchase.product_uom_id.name))
                if purchase.product_uom_id.compare(purchase.qty_invoiced, purchase.qty_received) > 0:
                    difference = True
                    rows.append(_('Revisar: las facturas vinculadas, incluidos borradores, superan la cantidad recibida.'))
            invoice.purchase_receipt_comparison = '\n'.join(rows)
            invoice.purchase_receipt_has_difference = difference

    def _post(self, soft=True):
        purchase_lines = self.filtered(lambda move: move.move_type == 'in_invoice').invoice_line_ids.purchase_line_id
        if purchase_lines:
            self.env['res.company']._with_locked_records(purchase_lines.sorted('id'))
            purchase_lines.invalidate_recordset(['invoice_lines', 'qty_received'])
        for purchase_line in purchase_lines:
            company = purchase_line.company_id
            if company.purchase_receipt_difference_policy != 'block' or not purchase_line.product_id.is_storable:
                continue
            relevant = purchase_line.invoice_lines.filtered(
                lambda line: line.move_id.state == 'posted' or line.move_id in self)
            billed = sum(
                line.product_uom_id._compute_quantity(line.quantity, purchase_line.product_uom_id)
                * (-1 if line.move_id.move_type == 'in_refund' else 1)
                for line in relevant
            )
            if purchase_line.product_uom_id.compare(billed, purchase_line.qty_received) > 0:
                raise ValidationError(_('La cantidad facturada de %(product)s supera lo recibido de %(order)s. '
                                        'Revisa Compra y recepción o la política de diferencias en Inventario.',
                                        product=purchase_line.product_id.display_name, order=purchase_line.order_id.name))
        bills = self.filtered(lambda move: move.move_type == 'in_invoice' and move.ref and move.state == 'draft')
        for bill in bills.sorted(lambda move: move.commercial_partner_id.id):
            partner = bill.commercial_partner_id
            # Serialize duplicate-reference checks for the same legal supplier.
            self.env.cr.execute('SELECT id FROM res_partner WHERE id = %s FOR UPDATE', [partner.id])
        duplicates = bills._fetch_duplicate_reference(matching_states=('posted',))
        for bill in bills:
            if duplicates.get(bill):
                raise ValidationError(_('Ya existe una factura contabilizada de este proveedor con la referencia %s.', bill.ref))
        if bills and len(bills) != len({(bill.company_id.id, bill.commercial_partner_id.id, bill.currency_id.id,
                                         bill.ref, bill.invoice_date.year if bill.invoice_date else None) for bill in bills}):
            raise ValidationError(_('El lote contiene referencias de factura duplicadas para el mismo proveedor.'))
        return super()._post(soft=soft)
