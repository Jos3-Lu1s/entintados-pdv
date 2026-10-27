"""Pruebas de devolución/nota de crédito; el ejecutable revierte toda la transacción."""
from contextlib import ExitStack
from unittest.mock import patch

from odoo import Command, api, fields, SUPERUSER_ID
from odoo.exceptions import UserError


def run_return_credit_checks(env):
    company = env.company

    def account(kind, code):
        found = env['account.account'].search([
            ('account_type', '=', kind), ('company_ids', 'in', company.ids),
        ], limit=1)
        return found or env['account.account'].create({
            'name': 'Return credit ' + kind, 'code': code, 'account_type': kind,
            'company_ids': [Command.set(company.ids)], 'reconcile': kind == 'asset_receivable',
        })

    revenue = account('income', '990081')
    receivable = account('asset_receivable', '990082')
    journal = env['account.journal'].create({
        'name': 'Return credit regression', 'code': 'RTCR', 'type': 'sale',
        'company_id': company.id, 'default_account_id': revenue.id,
    })
    partner = env['res.partner'].create({
        'name': 'Return credit regression', 'phone': '5551234567', 'is_customer': True,
        'property_account_receivable_id': receivable.id,
    })
    tax = env['account.tax'].create({
        'name': 'Return tax regression', 'amount_type': 'percent', 'amount': 16,
        'type_tax_use': 'sale', 'company_id': company.id,
    })
    products = env['product.product'].create([
        {'name': name, 'type': 'consu', 'is_storable': True, 'list_price': price,
         'property_account_income_id': revenue.id, 'taxes_id': [Command.set(tax.ids)]}
        for name, price in [('Returned product regression', 100), ('Unreturned product regression', 50)]
    ])
    warehouse = env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
    assert warehouse, 'No hay almacén para las pruebas'
    customer = env.ref('stock.stock_location_customers')
    for product in products:
        env['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 100)
    order = env['sale.order'].create({'partner_id': partner.id, 'company_id': company.id})
    sale_lines = env['sale.order.line'].create([
        {'order_id': order.id, 'product_id': product.id, 'product_uom_qty': quantity,
         'price_unit': price, 'tax_ids': [Command.set(tax.ids)]}
        for product, quantity, price in [(products[0], 5, 100), (products[1], 2, 50)]
    ])

    def invoice_for(lines, quantities=None):
        invoice = env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id, 'journal_id': journal.id,
            'company_id': company.id, 'invoice_date': fields.Date.context_today(order),
            'invoice_line_ids': [Command.create({
                'product_id': line.product_id.id, 'name': line.name,
                'account_id': revenue.id, 'quantity': (quantities or {}).get(line.id, line.product_uom_qty),
                'product_uom_id': line.product_uom_id.id, 'price_unit': line.price_unit,
                'discount': 10, 'tax_ids': [Command.set(tax.ids)],
                'sale_line_ids': [Command.set(line.ids)],
            }) for line in lines],
        })
        # These sale-linked fixtures exercise returns, without sending CFDI.
        with patch.object(type(invoice), '_l10n_mx_edi_cfdi_invoice_try_send', return_value=None):
            invoice.action_post()
        return invoice

    invoice = invoice_for(sale_lines)
    delivery = env['stock.picking'].create({
        'picking_type_id': warehouse.out_type_id.id, 'company_id': company.id,
        'location_id': warehouse.lot_stock_id.id, 'location_dest_id': customer.id,
        'partner_id': partner.id,
        'move_ids': [Command.create({
            'product_id': line.product_id.id, 'product_uom_qty': line.product_uom_qty,
            'product_uom': line.product_uom_id.id, 'sale_line_id': line.id,
            'location_id': warehouse.lot_stock_id.id, 'location_dest_id': customer.id,
        }) for line in sale_lines],
    })
    delivery.action_confirm()
    delivery.action_assign()
    for move in delivery.move_ids:
        move.quantity = move.product_uom_qty
        move.picked = True
    result = delivery.with_context(skip_backorder=True, skip_sms=True).button_validate()
    assert delivery.state == 'done', (delivery.state, result)
    assert not delivery.return_credit_note_ids, 'Una entrega no debe generar notas de crédito'

    def prepare_return(quantity, selected_invoice=None):
        wizard = env['stock.return.picking'].create({'picking_id': delivery.id})
        for line in wizard.product_return_moves:
            line.quantity = quantity if line.product_id == products[0] else 0
        returned = wizard._create_return()
        if selected_invoice:
            returned.return_invoice_id = selected_invoice
        return returned

    returned = prepare_return(3)
    assert not returned.return_credit_note_ids, 'No generar notas antes de validar'
    returned.move_ids.quantity = 2
    returned.move_ids.picked = True
    returned.with_context(skip_backorder=True, skip_sms=True).button_validate()
    assert returned.state == 'done'
    note = returned.return_credit_note_ids
    assert len(note) == 1 and note.state == 'draft' and note.move_type == 'out_refund'
    assert note.reversed_entry_id == invoice and note.stock_return_picking_id == returned
    lines = note.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
    source = invoice.invoice_line_ids.filtered(lambda line: line.product_id == products[0])
    assert lines.product_id == invoice._reajuste_product()
    assert len(lines) == 1 and lines.quantity == 2 and lines.return_source_quantity == 2
    assert lines.price_unit == source.price_unit and lines.discount == source.discount
    assert lines.tax_ids == source.tax_ids and lines.stock_return_source_line_id == source
    assert note.currency_id.is_zero(note.amount_total - source.price_total * 2 / source.quantity)
    assert returned._create_return_credit_notes() == note, 'Reintento duplicó la nota'
    assert invoice.state == 'posted', 'La factura original no debe cancelarse'

    second = prepare_return(1)
    second.move_ids.quantity = 1
    second.move_ids.picked = True
    second.with_context(skip_backorder=True, skip_sms=True).button_validate()
    assert second.return_credit_note_ids.invoice_line_ids.filtered(
        lambda line: line.stock_return_source_line_id == source).return_source_quantity == 1
    assert source.move_id._return_line_capacities()[source.id] == 2

    # Las notas previas en borrador también consumen el límite reembolsable.
    previous = invoice._reverse_moves(default_values_list=[{'date': fields.Date.context_today(order)}])
    for line in previous.invoice_line_ids:
        if line.product_id == products[0]:
            line.quantity = 2
        else:
            line.unlink()
    assert invoice._return_line_capacities()[source.id] == 0
    excess = prepare_return(1)
    excess.move_ids.quantity = 1
    excess.move_ids.picked = True
    try:
        with env.cr.savepoint():
            excess.with_context(skip_backorder=True, skip_sms=True).button_validate()
    except UserError:
        pass
    else:
        raise AssertionError('Se permitió acreditar más de lo facturado')
    assert excess.state != 'done', 'El fallo debe revertir la validación del inventario'
    previous.button_cancel()

    # Varias facturas para el mismo producto: no elegir una arbitrariamente.
    extra_invoice = invoice_for(sale_lines[:1], {sale_lines[0].id: 1})
    try:
        with env.cr.savepoint():
            excess.with_context(skip_backorder=True, skip_sms=True).button_validate()
    except UserError as error:
        assert 'varias facturas' in str(error)
    else:
        raise AssertionError('Faltó solicitar la factura de origen cuando había ambigüedad')
    excess.return_invoice_id = extra_invoice
    excess.with_context(skip_backorder=True, skip_sms=True).button_validate()
    assert excess.return_credit_note_ids.reversed_entry_id == extra_invoice
    # Conversión entre la unidad facturada y la unidad del movimiento físico.
    source_uom = source.product_uom_id
    quantity_in_source = products[0].uom_id._compute_quantity(2, source_uom, round=False)
    assert quantity_in_source == lines.return_source_quantity
    assert invoice.has_return_credit and invoice.return_credit_count == 2
    assert note.action_return_invoices()['res_id'] == invoice.id
    assert note.action_return_pickings()['res_id'] == returned.id
    assert invoice in env['account.move'].search([('has_return_credit', '=', True)])
    return 'PASS: devolución parcial, cantidades realizadas, productos, precio/descuento/impuestos, factura original, borrador, reintentos, notas previas, límite y selección de factura.'


def run():
    import os
    import sys
    from pathlib import Path
    from odoo.tools import config
    from odoo.orm.registry import Registry
    from odoo.service.db import list_dbs

    config.parse_config(['-c', '/etc/odoo/odoo.conf', '--no-http'])
    # La imagen oficial recibe la conexión por argumentos del entrypoint,
    # que no necesariamente están escritos en odoo.conf. Nunca imprimirlos.
    keys = {'--db_host': 'db_host', '--db_port': 'db_port',
            '--db_user': 'db_user', '--db_password': 'db_password'}
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            argv = [arg.decode() for arg in path.read_bytes().split(b'\0') if arg]
        except (OSError, UnicodeDecodeError):
            continue
        if not any(Path(arg).name in ('odoo', 'odoo-bin') for arg in argv[:2]):
            continue
        for index, arg in enumerate(argv):
            if arg in keys and index + 1 < len(argv):
                config[keys[arg]] = argv[index + 1]
            elif '=' in arg and arg.split('=', 1)[0] in keys:
                name, value = arg.split('=', 1)
                config[keys[name]] = value
        break
    for env_name, key in [('HOST', 'db_host'), ('PORT', 'db_port'),
                          ('USER', 'db_user'), ('PASSWORD', 'db_password')]:
        if not config[key] and os.environ.get(env_name):
            config[key] = os.environ[env_name]
    databases = list_dbs(force=True)
    if '--list-databases' in sys.argv:
        print('Bases locales disponibles:', ', '.join(databases))
        return
    database = sys.argv[sys.argv.index('--database') + 1] if '--database' in sys.argv else 'Osel_Test'
    if database not in databases:
        raise RuntimeError('No se encontró la base de pruebas indicada; no se modificó otra base.')
    registry = Registry(database)
    with registry.cursor() as cr:
        try:
            # Inicializar las columnas nuevas solo dentro de esta transacción;
            # no actualizar el módulo ni modificar vistas de la base en servicio.
            registry.init_models(cr, ['account.move', 'account.move.line', 'stock.picking'],
                                 {'module': 'entintados_pdv'}, install=False)
            env = api.Environment(cr, SUPERUSER_ID, {'tracking_disable': True, 'lang': 'en_US'})
            with ExitStack() as stack:
                for name in ('_l10n_mx_edi_cfdi_invoice_try_send', '_l10n_mx_edi_cfdi_invoice_try_cancel'):
                    if hasattr(type(env['account.move']), name):
                        stack.enter_context(patch.object(type(env['account.move']), name,
                            side_effect=AssertionError('Una prueba no debe contactar al PAC')))
                print(run_return_credit_checks(env))
                from odoo.tools.convert import convert_file
                convert_file(env, 'entintados_pdv', 'views/stock_return_credit_views.xml', {}, mode='update')
                print('PASS: vistas de devolución y nota de crédito validadas por Odoo.')
        finally:
            cr.rollback()
            print('ROLLBACK: datos y cambios de esquema de la prueba revertidos.')


if __name__ == '__main__':
    run()
