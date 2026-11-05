"""Exercise native POS invoice preparation; all fixtures are rolled back."""
import os
import sys

from odoo import Command, api, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.tools import config

database = sys.argv[1] if len(sys.argv) > 1 else 'Osel_Test'
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http'])
for variable, key in [('HOST', 'db_host'), ('USER', 'db_user'), ('PASSWORD', 'db_password')]:
    if not config[key] and os.environ.get(variable):
        config[key] = os.environ[variable]
with Registry(database).cursor() as cr:
    try:
        env = api.Environment(cr, SUPERUSER_ID, {})
        session = env['pos.session'].search([], limit=1)
        assert session
        base = env['product.product'].search([('tint_role', '=', 'base')], limit=1).copy({
            'name': 'Base factura QA', 'default_code': 'BASE-QA'})
        order = env['pos.order'].create({
            'session_id': session.id, 'amount_tax': 0, 'amount_total': 100,
            'amount_paid': 0, 'amount_return': 0,
            'lines': [Command.create({'product_id': base.id, 'qty': 1, 'price_unit': 100,
                'price_subtotal': 100, 'price_subtotal_incl': 100,
                'tint_color_code': 'COLOR-QA', 'customer_note': 'Color: [COLOR-QA] Azul QA',
                'tax_ids': [Command.clear()]})]})
        line = order.lines
        expected = '[BASE-QA] Base factura QA + [COLOR-QA] Azul QA'

        def values():
            return [command[2] for command in order._prepare_invoice_lines('out_invoice')]

        result = values()
        assert result[0]['name'] == expected, result
        assert not any(item.get('display_type') == 'line_note' for item in result), result
        assert result[0]['quantity'] == 1 and result[0]['price_unit'] == 100
        colorant = env['product.product'].search([('tint_role', '=', 'colorant')], limit=1)
        assert colorant
        env['pos.order.line'].create({
            'order_id': order.id, 'product_id': colorant.id, 'combo_parent_id': line.id,
            'qty': 1, 'price_unit': 0, 'price_subtotal': 0, 'price_subtotal_incl': 0,
            'tax_ids': [Command.clear()],
        })
        invoice = env['account.move'].create({
            'move_type': 'out_invoice', 'company_id': order.company_id.id,
            'partner_id': env.company.partner_id.id,
            'invoice_line_ids': order._prepare_invoice_lines('out_invoice'),
        })
        order.account_move = invoice
        components = invoice.invoice_line_ids.filtered(lambda item: item.product_id == colorant)
        assert components
        assert not components & invoice._get_move_lines_to_report()
        assert components in invoice.invoice_line_ids
        assert invoice.amount_total == 100
        from odoo.tools.convert import convert_file
        convert_file(env, 'entintados_pdv', 'report/invoice_tint.xml', {}, mode='update')
        html, _ = env['ir.actions.report']._render_qweb_html('account.account_invoices', invoice.ids)
        assert expected.encode() in html
        assert colorant.display_name.encode() not in html
        row = env['tint.sales.report'].new({'pos_order_id': order.id})
        action = row.action_open_document()
        assert action['res_model'] == 'pos.order' and action['res_id'] == order.id
        assert action['views'] == [(False, 'form')]
        sale = env['sale.order'].create({'partner_id': env.company.partner_id.id})
        row = env['tint.sales.report'].new({'sale_order_id': sale.id})
        action = row.action_open_document()
        assert action['res_model'] == 'sale.order' and action['res_id'] == sale.id
        assert action['views'] == [(False, 'form')]
        convert_file(env, 'entintados_pdv', 'views/tint_sales_report_views.xml', {}, mode='update')
        view = env.ref('entintados_pdv.view_tint_sales_report_list')
        assert 'widget="tint_document_link"' in view.arch_db
        assert 'name="action_open_document"' not in view.arch_db
        print('PASS: recipe hidden in rendered invoice, accounting totals intact, POS and quotation navigation.')
        (order.lines - line).unlink()
        line.customer_note += '\nPara fachada'
        result = values()
        assert result[0]['name'] == expected
        assert [item['name'] for item in result if item.get('display_type') == 'line_note'] == ['Para fachada']
        line.write({'tint_color_code': False, 'customer_note': 'Para fachada'})
        result = values()
        assert ' + ' not in result[0]['name']
        assert result[-1]['name'] == 'Para fachada'
        line.write({'customer_note': 'Color: [OLD-QA] Color anterior'})
        assert line._tint_invoice_description()[0] == ''
        line.tint_color_code = 'OLD-QA'
        assert line._tint_invoice_description()[0].endswith('+ [OLD-QA] Color anterior')
        print('PASS: base + color, no duplicated note, other notes retained, amounts unchanged, ordinary base unchanged, legacy code supported.')
    finally:
        cr.rollback()
        print('ROLLBACK: invoice description fixtures reverted.')
