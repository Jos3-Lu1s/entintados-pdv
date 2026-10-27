"""Read-only report integration checks; run inside the Odoo container."""
import logging

from odoo import api, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.tools import config


config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
Registry('Osel_Test')
from odoo.addons.entintados_pdv.models.pos_sale_details import allocate_amount as allocate
assert allocate(100, [1, 1, 1], lambda n: round(n, 2)) == [33.33, 33.33, 33.34]
assert allocate(-100, [1, 1], lambda n: round(n, 2)) == [-50, -50]
assert allocate(5000, [9488.58], lambda n: round(n, 2)) == [5000]

with Registry('Osel_Test').cursor() as cr:
    env = api.Environment(cr, SUPERUSER_ID, {})
    report = env['report.point_of_sale.report_saledetails']
    sessions = env['pos.order'].search(
        [('state', 'in', ['paid', 'done'])], order='id desc', limit=100
    ).session_id[:5]
    assert sessions, 'Se requiere una sesión para verificar el reporte'
    checked = 0
    for session in sessions:
        result = report.get_sale_details(session_ids=session.ids)
        orders = env['pos.order'].search(report._get_domain(session_ids=session.ids))
        expected = {}
        for payment in orders.payment_ids:
            key = (payment.payment_method_id.name, payment.pos_order_id.currency_id.id)
            expected[key] = expected.get(key, 0.0) + payment.amount
        for group in result['invoice_payment_groups']:
            currency = group['currency']
            assert currency.is_zero(sum(row['amount'] for row in group['rows']) - group['total']), (group['name'], sum(row['amount'] for row in group['rows']), group['total'])
            assert currency.is_zero(group['total'] - expected[(group['name'], currency.id)])
            checked += len(group['rows'])
        html, _ = env['ir.actions.report']._render_qweb_html(
            'point_of_sale.report_saledetails', session.ids)
        if result['invoice_payment_groups']:
            assert b'invoice_payments_detail' in html
    cr.rollback()
    print(f'OK: reparto y redondeo; {len(sessions)} sesiones; {checked} filas; HTML generado; sin cambios de datos.')
