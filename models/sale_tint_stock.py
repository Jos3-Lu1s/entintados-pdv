from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .sale_tint import _TINT_OPERATION


class StockMove(models.Model):
    _inherit = 'stock.move'

    tint_base_move_id = fields.Many2one(
        'stock.move', string='Salida de base entintada', readonly=True, copy=False,
        check_company=True, index=True, ondelete='restrict')

    _tint_consumption_unique = models.Constraint(
        'UNIQUE(tint_base_move_id, product_id)',
        'El colorante ya se descontó para esta salida de base.')

    @api.depends('product_id', 'picking_type_id', 'description_picking_manual', 'sale_line_id',
                 'sale_line_id.tint_snapshot', 'pos_tint_sale_line_id', 'origin_returned_move_id')
    def _compute_description_picking(self):
        super()._compute_description_picking()
        for move in self:
            if move.description_picking_manual:
                continue
            line = move._tint_sale_line()
            if line and move.product_id.id == line._tint_recipe().get('base_id'):
                # The native delivery report already prints the base product separately.
                move.description_picking = line.tint_color_description or ''

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('_tint_operation') is not _TINT_OPERATION and any(
                vals.get('tint_base_move_id') for vals in vals_list):
            raise ValidationError(_('El consumo de colorante se genera desde la salida de la base.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'tint_base_move_id' in vals and self.env.context.get('_tint_operation') is not _TINT_OPERATION:
            raise ValidationError(_('No se puede cambiar el origen de un consumo de colorante.'))
        return super().write(vals)

    def _action_done(self, cancel_backorder=False):
        bases = self.filtered(lambda move: move.state not in ('done', 'cancel')
                              and (move.location_dest_id.usage == 'customer'
                                   or (move.location_id.usage == 'customer' and move.origin_returned_move_id))
                              and bool(move._tint_sale_line()))
        if not bases:
            return super()._action_done(cancel_backorder=cancel_backorder)
        # Reservation failures must also roll back the base's completed move/backorder.
        with self.env.cr.savepoint():
            self.env['res.company']._with_locked_records(bases.sorted('id'))
            bases.invalidate_recordset(['state', 'quantity', 'move_line_ids'])
            pending = bases.filtered(lambda move: move.state not in ('done', 'cancel'))
            result = super()._action_done(cancel_backorder=cancel_backorder)
            pending.filtered(lambda move: move.state == 'done')._consume_tint_colorants()
            return result

    def _tint_sale_line(self):
        self.ensure_one()
        move = self
        while move:
            line = move.sale_line_id
            if line and line._tint_recipe().get('base_only'):
                return line
            move = move.origin_returned_move_id
        return self.env['sale.order.line']

    def _is_tint_consumption(self):
        self.ensure_one()
        move = self
        while move:
            if move.tint_base_move_id:
                return True
            move = move.origin_returned_move_id
        return False

    def _action_synch_order(self):
        # Native sale_stock sells extra picking products automatically. Formula
        # consumption (including returns of it) must never create a sales line.
        regular = self.filtered(lambda move: not move._is_tint_consumption())
        return super(StockMove, regular)._action_synch_order()

    def _consume_tint_colorants(self):
        point = self.env.ref('entintados_pdv.uom_tint_point')
        for base_move in self:
            sale_line = base_move._tint_sale_line()
            snapshot = sale_line._tint_recipe()
            containers = base_move.product_uom._compute_quantity(
                base_move.quantity, sale_line.product_uom_id, round=False)
            warehouse_stock = base_move.picking_type_id.warehouse_id.lot_stock_id
            is_return = base_move.location_id.usage == 'customer'
            source = base_move.location_id if is_return else warehouse_stock or base_move.location_id
            destination = (warehouse_stock or base_move.location_dest_id) if is_return else base_move.location_dest_id
            for dose in snapshot['doses']:
                product = self.env['product.product'].browse(dose['product_id'])
                if not product.active or not product.is_storable or not product.uom_id._has_common_reference(point):
                    raise UserError(_('Revisa el inventario y la unidad del colorante %s.') % product.display_name)
                previous = self.search([
                    ('tint_base_move_id', '=', base_move.origin_returned_move_id.id),
                    ('product_id', '=', product.id), ('state', '=', 'done'),
                ], limit=1) if base_move.origin_returned_move_id else self.browse()
                consumption = self.with_context(_tint_operation=_TINT_OPERATION).create({
                    'product_id': product.id, 'product_uom': point.id,
                    'product_uom_qty': dose['points'] * containers,
                    'location_id': source.id, 'location_dest_id': destination.id,
                    # An independent inventory consumption is not a delivered sale item.
                    # Link it to its base move, rather than adding it to the commercial picking.
                    'state': 'confirmed', 'picking_type_id': base_move.picking_type_id.id,
                    'company_id': base_move.company_id.id, 'origin': base_move.origin,
                    'description_picking': _('Colorante de %s') % snapshot.get('color_name', snapshot.get('name', '')),
                    'tint_base_move_id': base_move.id,
                    'origin_returned_move_id': previous.id,
                })
                consumption._action_assign()
                if point.compare(consumption.quantity, consumption.product_uom_qty) < 0:
                    raise UserError(_('No hay suficiente colorante disponible para validar la salida: %s.') % product.display_name)
                consumption.picked = True
                consumption._action_done(cancel_backorder=True)
            base_move.picking_id.invalidate_recordset(['tint_consumption_count'])


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    tint_consumption_count = fields.Integer(string='Movimientos de colorante', compute='_compute_tint_consumption_count')

    def _tint_consumption_moves(self):
        return self.env['stock.move'].search([('tint_base_move_id', 'in', self.move_ids.ids)])

    @api.depends('move_ids.state')
    def _compute_tint_consumption_count(self):
        for picking in self:
            picking.tint_consumption_count = len(picking._tint_consumption_moves())

    def action_view_tint_consumptions(self):
        self.ensure_one()
        self.check_access('read')
        return {
            'type': 'ir.actions.act_window', 'name': _('Colorantes del entintado'),
            'res_model': 'stock.move', 'view_mode': 'list,form',
            'domain': [('tint_base_move_id', 'in', self.move_ids.ids)],
            'context': {'create': False},
        }
