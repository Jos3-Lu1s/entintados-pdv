"""Compile POS production assets after adding inline invoice options; rollback."""
import os
import sys
from pathlib import Path
from lxml import etree
import odoo
if not hasattr(odoo, 'Command'):
    import odoo.init
from odoo import api, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.tools import config

database = sys.argv[1]
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http',
                     '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
                     '--db_password=' + os.environ['PASSWORD']])
with Registry(database).cursor() as cr:
    try:
        env = api.Environment(cr, SUPERUSER_ID, {})
        native = etree.parse('/usr/lib/python3/dist-packages/odoo/addons/point_of_sale/static/src/app/screens/payment_screen/payment_screen.xml')
        custom = etree.parse(str(Path(__file__).resolve().parents[1] /
                                 'static/src/app/overrides/customer_payment_summary.xml'))
        namespace = etree.FunctionNamespace(None)
        namespace['hasclass'] = lambda context, *names: all(
            name in context.context_node.get('class', '').split() for name in names)
        for extension in custom.xpath('//t[@t-inherit]'):
            targets = native.xpath('//t[@t-name=$name]', name=extension.get('t-inherit'))
            assert len(targets) == 1, extension.get('t-inherit')
            parent = etree.fromstring(etree.tostring(targets[0]))
            for instruction in extension.findall('xpath'):
                assert parent.xpath(instruction.get('expr')), (extension.get('t-inherit'), instruction.get('expr'))
        print('PASS: every payment template XPath matches its actual native parent, including the invoice button.')
        bundle = env['ir.qweb']._get_asset_bundle('point_of_sale.assets_prod', css=True, js=True)
        attachment = bundle.js()
        assert b'@entintados_pdv/app/utils/invoice_options' in attachment.raw
        assert b'changePaymentInvoiceUsage' in attachment.raw
        assert b'@entintados_pdv/app/utils/quotation_print' in attachment.raw
        assert b'onClickPrintSaleQuotation' in attachment.raw
        bundle.css()
        print('PASS: production POS JavaScript and styles compile with invoice options.')
    finally:
        cr.rollback()
