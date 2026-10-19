import logging
from collections import defaultdict

from odoo import fields, models, _
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class StockPickingInvoiceCancellation(models.Model):
    _inherit = 'stock.picking'

    cancelled_invoice_id = fields.Many2one(
        'account.move', string='Factura cancelada', copy=False,
        index=True, check_company=True, readonly=True,
    )


class AccountMoveInventoryCancellation(models.Model):
    _inherit = 'account.move'

    cancellation_return_ids = fields.One2many(
        'stock.picking', 'cancelled_invoice_id', string='Devoluciones por cancelación',
    )

    def _l10n_mx_edi_cfdi_invoice_post_cancel(self):
        result = super()._l10n_mx_edi_cfdi_invoice_post_cancel()
        for invoice in self:
            if invoice.move_type != 'out_invoice' or invoice.l10n_mx_edi_cfdi_state != 'cancel':
                continue
            try:
                # Only stock work is inside the savepoint. The PAC call must
                # remain outside because native fiscal methods may commit.
                with self.env.cr.savepoint():
                    invoice.sudo()._entintados_return_cancelled_invoice_products()
            except Exception:
                _logger.exception('Inventory return failed for cancelled invoice id %s', invoice.id)
                invoice.message_post(body=_(
                    'El CFDI fue cancelado, pero no se completó la devolución de inventario. '
                    'Revise las entregas y utilice Reintentar devolución de inventario.'
                ))
        return result

    def action_retry_cancellation_inventory_return(self):
        self.check_access('write')
        for invoice in self:
            if invoice.move_type != 'out_invoice' or invoice.l10n_mx_edi_cfdi_state != 'cancel':
                raise UserError(_('La devolución requiere una factura de cliente con CFDI cancelado.'))
            invoice.sudo()._entintados_return_cancelled_invoice_products()
        return self.action_view_cancellation_returns()

    def action_view_cancellation_returns(self):
        return {
            'type': 'ir.actions.act_window', 'name': _('Devoluciones por cancelación'),
            'res_model': 'stock.picking', 'view_mode': 'list,form',
            'domain': [('cancelled_invoice_id', 'in', self.ids)],
        }

    def _entintados_cancellation_return_plan(self):
        self.ensure_one()
        quotas = defaultdict(float)
        for line in self.invoice_line_ids:
            if line.display_type != 'product' or line.product_id.type == 'service' or line.quantity <= 0:
                continue
            key = (line.product_id.id, tuple(sorted(line.sale_line_ids.ids)))
            quotas[key] += line.product_uom_id._compute_quantity(line.quantity, line.product_id.uom_id)
        orders = self.env['pos.order'].search([
            ('company_id', '=', self.company_id.id),
            '|', ('account_move', '=', self.id), ('split_invoice_ids', 'in', self.ids),
        ])
        pos_moves = orders.picking_ids.move_ids
        own_returns = self.cancellation_return_ids.filtered(lambda p: p.state != 'cancel').move_ids
        plan = defaultdict(dict)
        allocated = defaultdict(float)
        for (product_id, sale_ids), needed in quotas.items():
            if sale_ids:
                deliveries = self.env['stock.move'].search([
                    ('sale_line_id', 'in', list(sale_ids)),
                    ('product_id', '=', product_id), ('company_id', '=', self.company_id.id),
                    ('state', '=', 'done'), ('location_dest_id.usage', '=', 'customer'),
                    ('location_id.usage', '!=', 'customer'),
                ], order='id')
            else:
                deliveries = pos_moves.filtered(
                    lambda m: m.product_id.id == product_id and m.state == 'done'
                    and m.location_dest_id.usage == 'customer' and m.location_id.usage != 'customer'
                ).sorted('id')
            # Count previously generated returns even if still awaiting validation.
            for returned in own_returns.filtered(lambda m: m.origin_returned_move_id in deliveries):
                qty = returned.quantity if returned.state == 'done' else returned.product_uom_qty
                needed -= returned.product_uom._compute_quantity(qty, returned.product_id.uom_id)
            for move in deliveries:
                product_uom = move.product_id.uom_id
                if product_uom.compare(needed, 0) <= 0:
                    break
                available = move.quantity
                for returned in move.returned_move_ids.filtered(lambda m: m.state != 'cancel'):
                    qty = returned.quantity if returned.state == 'done' else returned.product_uom_qty
                    available -= returned.product_uom._compute_quantity(qty, move.product_uom)
                available = move.product_uom._compute_quantity(max(available, 0), product_uom)
                quantity = min(needed, max(available - allocated[move.id], 0))
                if product_uom.compare(quantity, 0) <= 0:
                    continue
                allocated[move.id] += quantity
                move_qty = product_uom._compute_quantity(quantity, move.product_uom)
                plan[move.picking_id.id][move.id] = plan[move.picking_id.id].get(move.id, 0) + move_qty
                needed -= quantity
        return plan

    def _entintados_return_cancelled_invoice_products(self):
        self.ensure_one()
        # Serialize cancellation/retry on this invoice and its source deliveries.
        self.env['res.company']._with_locked_records(self)
        plan = self._entintados_cancellation_return_plan()
        pickings = self.env['stock.picking'].browse(list(plan))
        self.env['res.company']._with_locked_records(pickings | self.cancellation_return_ids)
        # Recalculate after locking, to account for concurrent/manual returns.
        plan = self._entintados_cancellation_return_plan()
        for picking_id, quantities in plan.items():
            wizard = self.env['stock.return.picking'].create({'picking_id': picking_id})
            for line in wizard.product_return_moves:
                line.quantity = quantities.get(line.move_id.id, 0)
                if 'to_refund' in line._fields:
                    line.to_refund = True
            returned = wizard._create_return()
            returned.cancelled_invoice_id = self
            returned.message_post(body=_('Devolución por cancelación de la factura %s.', self.display_name))
        pending = self.cancellation_return_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
        for picking in pending:
            # Native return reservation preserves the original lots and serials.
            picking.action_assign()
            picking.move_ids.picked = True
            picking.with_context(skip_backorder=True).button_validate()
            if picking.state != 'done':
                raise UserError(_('La devolución %s requiere validación del almacén.', picking.display_name))
        if self.cancellation_return_ids:
            self.message_post(body=_('Devolución de inventario completada para la factura cancelada.'))

