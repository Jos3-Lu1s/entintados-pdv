"""Render the invoice design in a rolled-back transaction."""
import os
import sys
import odoo
if not hasattr(odoo, 'Command'):
    import odoo.init
from odoo import api, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.tools import config
from odoo.tools.convert import convert_file

database = sys.argv[1]
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http',
    '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
    '--db_password=' + os.environ['PASSWORD']])
with Registry(database).cursor() as cr:
    env = api.Environment(cr, SUPERUSER_ID, {})
    try:
        convert_file(env, 'entintados_pdv', 'report/invoice_mat.xml', {}, mode='update')
        convert_file(env, 'entintados_pdv', 'report/sale_mat.xml', {}, mode='update')
        if '--install' in sys.argv:
            cr.commit()
            print('Installed invoice templates in ' + database)
            raise SystemExit(0)
        invoice = env['account.move'].search([('move_type','=','out_invoice'),
            ('invoice_line_ids','!=',False)], limit=1)
        assert invoice, 'No customer invoice to render'
        from lxml import etree
        native_view = etree.parse('/usr/lib/python3/dist-packages/odoo/addons/mail/static/src/core/common/attachment_view.xml')
        assert native_view.xpath('//iframe[@t-ref="iframeViewerPdf"]')
        bundle = env['ir.qweb']._get_asset_bundle('web.assets_web', css=True, js=True)
        assert b'matPdfSource' in bundle.js().raw
        assert invoice._mat_report_header()['stage'] in ('Facturado', 'Factura en borrador', 'Cancelado')
        preview = invoice.action_get_mat_preview()
        assert '/entintados_pdv.report_invoice_live/' in preview['url']
        live, _ = env['ir.actions.report']._render_qweb_html('entintados_pdv.action_report_invoice_live', invoice.ids)
        assert b'mat_invoice_lines' in live
        sale = env['sale.order'].search([('state','in',['draft','sent']), ('order_line','!=',False)], limit=1)
        assert sale
        assert sale._mat_report_header()['stage'] == 'Cotización'
        sale_html, _ = env['ir.actions.report']._render_qweb_html('sale.action_report_saleorder', sale.ids)
        assert b'mat_sale_lines' in sale_html
        sale_pdf, _ = env['ir.actions.report']._render_qweb_pdf('sale.action_report_saleorder', sale.ids)
        with open('/mnt/extra-addons/modules/entintados_pdv/tmp/pdfs/sale_mat_preview.pdf', 'wb') as stream:
            stream.write(sale_pdf)
        html, _ = env['ir.actions.report']._render_qweb_html('account.account_invoices', invoice.ids)
        assert b'mat-invoice' in html
        assert b'invoice_logo_placeholder.png' in html
        from lxml import html as html_parser
        document = html_parser.fromstring(html)
        table = document.xpath('//table[@name="mat_invoice_lines"]')[0]
        count = len(table.xpath('./thead/tr/th'))
        for row in table.xpath('./tbody/tr[td and not(td[@colspan])]'):
            assert len(row.xpath('./td')) == count, 'Invoice column alignment'
        assert 'Valor unitario' in table.text_content()
        assert 'Impuestos' not in table.xpath('./thead')[0].text_content()
        assert 'Unidades' not in table.text_content()
        product = invoice.invoice_line_ids.filtered(lambda line: line.display_type == 'product')[:1]
        original = (invoice.amount_untaxed, invoice.amount_tax, invoice.amount_total)
        values = invoice._mat_invoice_line_values(product)
        if product.product_id.tint_role == 'base' and '+ [' in product.name:
            assert ' + ' in values['code']
        # Tax-included prices must display a net value, with a separate discount.
        tax = env['account.tax'].create({'name': 'IVA incluido QA', 'amount': 16,
            'type_tax_use': 'sale', 'price_include_override': 'tax_included'})
        if product.tax_ids:
            tax.tax_group_id = product.tax_ids[:1].tax_group_id
        probe = env['account.move.line'].new({'move_id': invoice.id, 'product_id': product.product_id.id,
            'price_unit': 116, 'quantity': 2, 'discount': 10, 'tax_ids': [(6, 0, tax.ids)]})
        result = invoice._mat_invoice_line_values(probe)
        assert abs(result['unit'] - 100) < .01, result
        assert abs(result['discount'] - 20) < .01, result
        assert original == (invoice.amount_untaxed, invoice.amount_tax, invoice.amount_total)
        from odoo import Command
        discounted = env['account.move'].create({'move_type': 'out_invoice',
            'partner_id': invoice.partner_id.id, 'journal_id': invoice.journal_id.id,
            'invoice_date': invoice.invoice_date,
            'invoice_line_ids': [Command.create({'product_id': product.product_id.id,
                'name': product.name, 'account_id': product.account_id.id,
                'quantity': 2, 'price_unit': 116, 'discount': 10,
                'tax_ids': [Command.set(tax.ids)]})]})
        summary = discounted._mat_invoice_discount_summary()
        assert abs(summary['discount'] - 20) < .01, summary
        assert abs(summary['gross'] - 200) < .01, summary
        discounted_html, _ = env['ir.actions.report']._render_qweb_html('account.account_invoices', discounted.ids)
        assert b'Descuento' in discounted_html
        with open('/mnt/extra-addons/modules/entintados_pdv/tmp/pdfs/invoice_mat_preview.html', 'wb') as stream:
            stream.write(html)
        pdf, _ = env['ir.actions.report']._render_qweb_pdf('account.account_invoices', invoice.ids)
        with open('/mnt/extra-addons/modules/entintados_pdv/tmp/pdfs/invoice_mat_preview.pdf', 'wb') as stream:
            stream.write(pdf)
        pdf, _ = env['ir.actions.report']._render_qweb_pdf('account.account_invoices', discounted.ids)
        with open('/mnt/extra-addons/modules/entintados_pdv/tmp/pdfs/invoice_mat_discount_preview.pdf', 'wb') as stream:
            stream.write(pdf)
        # Supplier credit notes continue using the original layout.
        vendor = env['account.move'].search([('move_type','=','in_refund')], limit=1)
        if vendor:
            html, _ = env['ir.actions.report']._render_qweb_html('account.account_invoices', vendor.ids)
            assert b'invoice_logo_placeholder.png' not in html
        print('PASS: invoice HTML/PDF rendered; supplier layout preserved')
    finally:
        cr.rollback()
