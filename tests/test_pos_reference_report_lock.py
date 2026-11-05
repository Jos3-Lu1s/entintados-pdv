"""Reproduce the report-asset FK deadlock and verify the branch-numbering fix.

Run inside Odoo's container. Every fixture is rolled back; no PAC call is made.
"""
import sys
import time

from psycopg2.errors import LockNotAvailable
from odoo import api, Command, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.tools import config

database = sys.argv[1] if len(sys.argv) > 1 else 'OSEL_UAT'
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http'])
registry = Registry(database)


def create_report_asset(company_id):
    with registry.cursor() as cr:
        cr.execute("SET LOCAL lock_timeout = '750ms'")
        cr.execute("SET LOCAL statement_timeout = '5s'")
        env = api.Environment(cr, SUPERUSER_ID, {})
        result = env['ir.attachment'].create({
            'name': 'POS report concurrency regression.css',
            'company_id': company_id, 'type': 'url',
            'url': '/pos-report-concurrency-regression.css', 'mimetype': 'text/css',
        })
        assert result.id
        cr.rollback()


with registry.cursor() as order_cr:
    env = api.Environment(order_cr, SUPERUSER_ID, {})
    company = env.company
    company_id = company.id
    order_cr.execute('SELECT id FROM res_company WHERE id = %s FOR UPDATE', [company_id])
    try:
        create_report_asset(company_id)
    except LockNotAvailable:
        print('PASS: el bloqueo anterior reproduce la espera al crear el adjunto del reporte.', flush=True)
    else:
        raise AssertionError('No se reprodujo el conflicto con el bloqueo anterior')
    order_cr.rollback()

with registry.cursor() as order_cr:
    env = api.Environment(order_cr, SUPERUSER_ID, {})
    company = env['res.company'].browse(company_id)
    number, reference = env['pos.order']._next_branch_order_reference(company)
    started = time.monotonic()
    create_report_asset(company_id)
    elapsed = time.monotonic() - started
    assert elapsed < 5, elapsed
    print(f'PASS: el adjunto se crea desde otra transacción en {elapsed:.3f}s con el nuevo bloqueo.', flush=True)
    with registry.cursor() as competitor:
        try:
            competitor.execute('SELECT id FROM res_company WHERE id = %s FOR NO KEY UPDATE NOWAIT', [company_id])
        except LockNotAvailable:
            competitor.rollback()
        else:
            raise AssertionError('La numeración de la sucursal dejó de estar serializada')
    next_number, next_reference = env['pos.order']._next_branch_order_reference(company)
    assert next_number == number + 1
    assert reference == f'PDV-{number:04d}'
    assert next_reference == f'PDV-{next_number:04d}'
    if '--pdf' in sys.argv:
        journal = env['account.journal'].search([('company_id', '=', company_id), ('type', '=', 'sale')], limit=1)
        income = env['account.account'].search([('company_ids', 'in', company.ids), ('account_type', '=', 'income')], limit=1)
        assert journal and income, 'Se requiere el diario de ventas y una cuenta de ingresos para la prueba PDF'
        invoice = env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': company.partner_id.id,
            'company_id': company_id, 'journal_id': journal.id,
            'invoice_line_ids': [Command.create({
                'name': 'PDF concurrency regression', 'account_id': income.id,
                'quantity': 1, 'price_unit': 100, 'tax_ids': [Command.clear()],
            })],
        })
        started = time.monotonic()
        content, kind = env['ir.actions.report']._render_qweb_pdf(
            'account.account_invoices', invoice.ids, data={'proforma': True})
        assert kind == 'pdf' and content.startswith(b'%PDF'), (kind, len(content))
        print(f'PASS: PDF completo generado en {time.monotonic()-started:.3f}s con la transacción del consecutivo abierta.', flush=True)
    order_cr.rollback()
    print('PASS: consecutivo y exclusión entre cajas preservados. Órdenes, facturas y consecutivos de prueba revertidos.', flush=True)
