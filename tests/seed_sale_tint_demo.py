"""An idempotent sales example, exclusively in the disposable training database."""
import json
import os

from odoo import api, Command, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.tools import config

from seed_flow_demo import DATABASE, get, remember


def seed(root):
    if root.cr.dbname != DATABASE:
        raise RuntimeError('This script is restricted to the training database.')
    existing = root.ref('entintados_pdv.training_20261028_sale_tint_quote', raise_if_not_found=False)
    if existing and existing.exists():
        return existing
    point = root.ref('entintados_pdv.uom_tint_point')
    white = root['tint.base.type'].search([('code', '=', 'W')], limit=1)
    gallon = root['tint.size'].search([('code', '=', 'G')], limit=1)
    income = get(root, 'income')

    def product(key, name, price, **extra):
        return remember(root, key, root['product.product'].create({
            'name': name, 'default_code': 'PRUEBA-TINT-' + key.upper(), 'list_price': price,
            'taxes_id': [Command.clear()], 'invoice_policy': 'order',
            'property_account_income_id': income.id, **extra,
        }))

    base = product('sales_tint_base', 'PRUEBA Base blanca galón', 200,
                   tint_role='base', tint_base_type_id=white.id, tint_size_id=gallon.id, is_storable=True)
    red = product('sales_tint_red', 'PRUEBA Colorante rojo', 2,
                  tint_role='colorant', uom_id=point.id, is_storable=True)
    brush = product('sales_tint_brush', 'PRUEBA Brocha Trupper', 50)
    gallery = root['tint.gallery'].search([], limit=1)
    color = remember(root, 'sales_tint_color', root['tint.color'].create({
        'name': 'PRUEBA Rojo Sangre', 'gallery_id': gallery.id}))
    formula = remember(root, 'sales_tint_formula', root['tint.color.formula'].create({
        'color_id': color.id, 'base_type_id': white.id, 'size_id': gallon.id,
        'line_ids': [Command.create({'colorant_id': red.id, 'points': 12})],
    }))
    location = root['stock.warehouse'].search([('company_id', '=', root.company.id)], limit=1).lot_stock_id
    for item in (base, red):
        root['stock.quant']._update_available_quantity(item, location, 100)
    user = get(root, 'user')
    env = api.Environment(root.cr, user.id, {'lang': user.lang or 'en_US', 'mail_notify_force_send': False})
    order = env['sale.order'].create({
        'partner_id': get(root, 'customer').id, 'quotation_ref': 'PRUEBA Entintado Rojo Sangre y brocha',
    })
    order._apply_tint(formula.with_env(env), base.with_env(env), 1)
    env['sale.order.line'].create({'order_id': order.id, 'product_id': brush.id})
    assert order.amount_total == 274
    remember(root, 'sale_tint_quote', order)
    return order


def exercise(order):
    """Verify real delivery/invoice APIs and restore the saved quotation afterwards."""
    class RollbackExercise(Exception):
        pass
    try:
        with order.env.cr.savepoint():
            order.action_confirm()
            for picking in order.picking_ids:
                picking.move_ids.write({'picked': True})
                for move in picking.move_ids:
                    move.quantity = move.product_uom_qty
                validation = picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
                assert picking.state == 'done', (picking.state, validation)
            invoice = order._create_invoices()
            assert invoice.state == 'posted' and invoice.amount_total == 274
            colorants = invoice.invoice_line_ids.filtered(lambda line: line.product_id.tint_role == 'colorant')
            assert not colorants
            base_line = invoice.invoice_line_ids.filtered(lambda line: line.product_id.tint_role == 'base')
            assert base_line.price_unit == 224 and '12 Pts.' in base_line.name
            consumption = order.picking_ids._tint_consumption_moves()
            assert consumption.quantity == 12 and consumption.state == 'done' and not consumption.sale_line_id, consumption.read(['quantity', 'state', 'sale_line_id', 'tint_base_move_id'])
            raise RollbackExercise()
    except RollbackExercise:
        pass


if __name__ == '__main__':
    config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', DATABASE, '--no-http',
        '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
        '--db_password=' + os.environ['PASSWORD']])
    with Registry(DATABASE).cursor() as cr:
        root = api.Environment(cr, SUPERUSER_ID, {})
        order = seed(root)
        user = get(root, 'user')
        exercise(order.with_user(user).with_context(lang=user.lang or 'en_US'))
        cr.commit()
        print(json.dumps({'order': order.name, 'id': order.id, 'state': order.state,
                          'total': order.amount_total, 'currency': order.currency_id.name, 'delivery_invoice_verified': True}, ensure_ascii=False))
