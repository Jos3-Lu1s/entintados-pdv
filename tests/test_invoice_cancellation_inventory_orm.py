"""Standalone stock regression on Osel_Test; no PAC calls and full rollback."""
from unittest.mock import patch
from odoo import api, SUPERUSER_ID
from odoo.tools import config
from odoo.modules.registry import Registry


def run():
    config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
    registry = Registry('Osel_Test')
    with registry.cursor() as cr:
        try:
            env = api.Environment(cr, SUPERUSER_ID, {})
            warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
            customer = env.ref('stock.stock_location_customers')
            product = env['product.product'].create({'name': 'Cancellation inventory regression', 'is_storable': True})
            order = env['sale.order'].create({'partner_id': env.company.partner_id.id})
            sale_line = env['sale.order.line'].create({
                'order_id': order.id, 'product_id': product.id, 'product_uom_qty': 10,
            })
            other = env['product.product'].create({'name': 'Unrelated product regression', 'is_storable': True})
            env['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 20)
            env['stock.quant']._update_available_quantity(other, warehouse.lot_stock_id, 5)

            def deliver(quantity, extra=False):
                picking = env['stock.picking'].create({
                    'picking_type_id': warehouse.out_type_id.id,
                    'location_id': warehouse.lot_stock_id.id, 'location_dest_id': customer.id,
                    'partner_id': order.partner_id.id,
                })
                move = env['stock.move'].create({
                    'product_id': product.id, 'product_uom_qty': quantity,
                    'product_uom': product.uom_id.id, 'picking_id': picking.id,
                    'location_id': picking.location_id.id, 'location_dest_id': customer.id,
                    'sale_line_id': sale_line.id,
                })
                if extra:
                    env['stock.move'].create({
                        'product_id': other.id, 'product_uom_qty': 2,
                        'product_uom': other.uom_id.id, 'picking_id': picking.id,
                        'location_id': picking.location_id.id, 'location_dest_id': customer.id,
                    })
                picking.action_confirm()
                picking.action_assign()
                picking.move_ids.picked = True
                picking.with_context(skip_backorder=True).button_validate()
                assert picking.state == 'done'
                return move

            first, second = deliver(3, extra=True), deliver(7)
            invoice = env['account.move'].create({
                'move_type': 'out_invoice', 'partner_id': order.partner_id.id,
                'invoice_line_ids': [(0, 0, {
                    'product_id': product.id, 'quantity': 10,
                    'sale_line_ids': [(6, 0, sale_line.ids)],
                })],
            })
            # Partial invoicing never returns the entire order/delivery.
            invoice.invoice_line_ids.quantity = 4
            plan = invoice._entintados_cancellation_return_plan()
            assert sum(sum(q.values()) for q in plan.values()) == 4, plan
            invoice.invoice_line_ids.quantity = 10

            # A previous manual return must reduce the available stock return.
            wizard = env['stock.return.picking'].create({'picking_id': second.picking_id.id})
            for line in wizard.product_return_moves:
                line.quantity = 2 if line.move_id == second else 0
            previous = wizard._create_return()
            previous.move_ids.picked = True
            previous.with_context(skip_backorder=True).button_validate()
            assert previous.state == 'done'
            plan = invoice._entintados_cancellation_return_plan()
            assert sum(sum(q.values()) for q in plan.values()) == 8, plan

            invoice._entintados_return_cancelled_invoice_products()
            returns = invoice.cancellation_return_ids
            assert len(returns) == 2, returns
            assert set(returns.mapped('state')) == {'done'}
            assert sum(returns.move_ids.mapped('quantity')) == 8
            assert returns.move_ids.product_id == product, 'Unrelated delivery products returned'
            assert env['stock.quant']._get_available_quantity(product, warehouse.lot_stock_id) == 20
            invoice._entintados_return_cancelled_invoice_products()
            assert invoice.cancellation_return_ids == returns, 'Duplicate return on retry'
            assert not invoice._entintados_cancellation_return_plan()

            # Pending acceptance must not generate stock returns.
            with patch.object(type(invoice), '_l10n_mx_edi_cfdi_move_post_cancel'), \
                 patch.object(type(invoice), '_entintados_return_cancelled_invoice_products') as return_products:
                invoice.l10n_mx_edi_cfdi_state = 'cancel_requested'
                invoice._l10n_mx_edi_cfdi_invoice_post_cancel()
                return_products.assert_not_called()
                invoice.l10n_mx_edi_cfdi_state = 'cancel'
                invoice._l10n_mx_edi_cfdi_invoice_post_cancel()
                return_products.assert_called_once()

            # Lot-tracked products keep their original lots through the native
            # reservation of customer returns, without fabricating lot numbers.
            product = env['product.product'].create({
                'name': 'Lot cancellation regression', 'is_storable': True, 'tracking': 'lot',
            })
            sale_line = env['sale.order.line'].create({
                'order_id': order.id, 'product_id': product.id, 'product_uom_qty': 10,
            })
            lot = env['stock.lot'].create({
                'name': 'CANCEL-REGRESSION-LOT', 'product_id': product.id, 'company_id': env.company.id,
            })
            env['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 10, lot_id=lot)
            tracked_move = deliver(10)
            tracked_invoice = env['account.move'].create({
                'move_type': 'out_invoice', 'partner_id': order.partner_id.id,
                'invoice_line_ids': [(0, 0, {
                    'product_id': product.id, 'quantity': 10,
                    'sale_line_ids': [(6, 0, sale_line.ids)],
                })],
            })
            tracked_invoice._entintados_return_cancelled_invoice_products()
            assert tracked_invoice.cancellation_return_ids.state == 'done'
            assert tracked_invoice.cancellation_return_ids.move_ids.move_line_ids.lot_id == lot
            assert env['stock.quant']._get_available_quantity(product, warehouse.lot_stock_id, lot_id=lot) == 10
            print('PASS: multiple deliveries, invoice quantity cap, prior manual returns, unrelated products, stock restored, idempotent retry, fiscal confirmation gate and original lot traceability. No PAC calls; rollback enforced.')
        finally:
            cr.rollback()


if __name__ == '__main__':
    run()
