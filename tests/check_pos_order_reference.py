"""Branch numbering and report references; transactional rollback."""
import logging
from odoo import api, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.tools import config

config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
with Registry('Osel_Test').cursor() as cr:
    env = api.Environment(cr, SUPERUSER_ID, {})
    Order = env['pos.order']
    existing = Order.search([])
    assert existing and all(order.branch_order_number for order in existing)
    assert len({(o.company_id.id, o.branch_order_number) for o in existing}) == len(existing)
    for order in existing:
        assert order.name == f'{order.company_id.name} - PDV-{order.branch_order_number:04d}'
    session = env['pos.session'].search([], limit=1)
    maximum = max(existing.filtered(lambda o: o.company_id == session.company_id).mapped('branch_order_number'), default=0)
    first, second = Order.create([
        {'session_id': session.id, 'name': 'Stale client reference',
         'amount_tax': 0, 'amount_total': 0, 'amount_paid': 0, 'amount_return': 0},
        {'session_id': session.id, 'amount_tax': 0, 'amount_total': 0,
         'amount_paid': 0, 'amount_return': 0},
    ])
    assert first.branch_order_number == maximum + 1
    assert second.branch_order_number == maximum + 2
    before = first.name
    first.write({'name': '/', 'branch_order_number': 0})
    assert first.name == before
    assert first.branch_order_number == maximum + 1
    other = env['res.company'].create({'name': 'Sucursal prueba consecutivo'})
    assert Order._next_branch_order_reference(other) == (1, 'Sucursal prueba consecutivo - PDV-0001')
    assert Order._next_branch_order_reference(other) == (2, 'Sucursal prueba consecutivo - PDV-0002')
    sessions = existing.filtered(lambda o: o.state in ('paid', 'done')).session_id[:1]
    result = env['report.point_of_sale.report_saledetails'].get_sale_details(session_ids=sessions.ids)
    rows = [row for group in result['invoice_payment_groups'] for row in group['rows']]
    assert rows and any(' - PDV-' in row['order_reference'] for row in rows)
    html, _ = env['ir.actions.report']._render_qweb_html('point_of_sale.report_saledetails', sessions.ids)
    assert b' - PDV-' in html
    print(f'OK: {len(existing)} referencias; consecutivo por sucursal, continuidad y sincronización; referencia en reporte. Pruebas nuevas revertidas.')
    cr.rollback()
