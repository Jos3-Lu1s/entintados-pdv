from odoo import api, models, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model_create_multi
    def create(self, values_list):
        confirmed = [values.get('state', self.env.context.get('default_state')) == 'sale'
                     for values in values_list]
        values_list = [dict(values, state='draft') if confirm else values
                       for values, confirm in zip(values_list, confirmed)]
        with self.env.cr.savepoint():
            orders = super().create(values_list)
            to_confirm = orders.browse([order.id for order, confirm in zip(orders, confirmed) if confirm])
            if to_confirm:
                to_confirm.action_confirm()
            return orders

    def action_confirm(self):
        # A failed delivery must leave neither a confirmed sale nor a partial
        # stock deduction when another caller catches the validation error.
        with self.env.cr.savepoint():
            result = super().action_confirm()
            self.filtered(lambda order: order.state == 'sale')._auto_validate_sale_deliveries()
            return result

    def _auto_validate_sale_deliveries(self):
        for order in self:
            order.check_access('write')
            # Confirmation authorizes this fulfillment only for the deliveries
            # linked to the accessible sale, also for sales-only users.
            pending = order.picking_ids.sudo().with_company(order.company_id).filtered(lambda picking:
                picking.state not in ('done', 'cancel')
                and picking.picking_type_id.code in ('internal', 'outgoing')
                and picking.location_id.usage != 'customer')
            while pending:
                progressed = False
                for picking in pending.sorted('id'):
                    picking.action_assign()
                    moves = picking.move_ids.filtered(lambda move: move.state not in ('done', 'cancel'))
                    if moves.filtered(lambda move: move.move_orig_ids.filtered(
                            lambda origin: origin.state not in ('done', 'cancel'))):
                        continue
                    for move in moves:
                        if move.product_uom.compare(move.quantity, move.product_uom_qty) < 0:
                            raise UserError(_('No se puede validar automáticamente la entrega %(delivery)s: '
                                              'falta material disponible para %(product)s.',
                                              delivery=picking.name, product=move.product_id.display_name))
                    moves.picked = True
                    picking.with_context(skip_sms=True, skip_backorder=True).button_validate()
                    if picking.state != 'done':
                        raise UserError(_('La entrega %s requiere completar sus datos de validación antes de confirmar la venta.',
                                          picking.name))
                    progressed = True
                pending = pending.filtered(lambda picking: picking.state not in ('done', 'cancel'))
                if pending and not progressed:
                    raise UserError(_('No se pudo completar la salida de material de %s: hay operaciones previas pendientes.',
                                      order.name))
