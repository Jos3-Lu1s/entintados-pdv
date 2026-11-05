"""Verify report amounts with native invoices and roll back all test fixtures."""
import logging
import os
import sys
import unittest
from unittest.mock import patch

import odoo.init
from odoo import Command
from odoo.modules.registry import Registry
from odoo.tools import config

database = sys.argv[1]
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http',
                     '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
                     '--db_password=' + os.environ['PASSWORD']])
Registry(database)
logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
from odoo.tests import common
from odoo.addons.entintados_pdv.tests.test_sale_tint import TestSaleTint
from odoo.addons.entintados_pdv.models.sale_tint import _TINT_OPERATION
common.get_db_name = lambda: database


class ReportAmounts(TestSaleTint):
    def test_report_tax_amounts(self):
        report = self.env['tint.sales.report']
        report.init()
        tax = self.env['account.tax'].create({
            'name': 'IVA reporte QA', 'amount': 16, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'company_id': self.env.company.id})
        sale = self._order()
        line = sale._apply_tint(self.formula, self.base, 2)
        line.with_context(_tint_operation=_TINT_OPERATION).write({
            'tax_ids': [Command.set(tax.ids)], 'discount': 10})
        # Confirm without requiring physical stock; this test only invoices.
        self.env['stock.quant']._update_available_quantity(self.base, sale.warehouse_id.lot_stock_id, 100)
        self.env['stock.quant']._update_available_quantity(self.colorant, sale.warehouse_id.lot_stock_id, 100)
        sale.action_confirm()
        with patch.object(type(self.env['account.move']), '_l10n_mx_edi_cfdi_invoice_try_send', return_value=None):
            invoice = sale._create_invoices()
            if invoice.state == 'draft':
                invoice.action_post()
        self.env.flush_all()
        row = report.search([('sale_order_id', '=', sale.id), ('invoiced', '=', True)])
        self.assertEqual(len(row), 1)
        self.assertAlmostEqual(row.sale_price, invoice.amount_untaxed, places=2)
        self.assertAlmostEqual(row.invoiced_amount, invoice.amount_total, places=2)
        self.assertAlmostEqual(row.sale_price, 403.20, places=2)
        self.assertAlmostEqual(row.invoiced_amount, 467.71, places=2)

        session = self.env['pos.session'].search([('company_id', '=', self.env.company.id)], limit=1)
        self.assertTrue(session)
        ticket = self.env['pos.order'].create({
            'session_id': session.id, 'partner_id': self.partner.id,
            'amount_total': 208.80, 'amount_tax': 28.80, 'amount_paid': 208.80, 'amount_return': 0,
            'lines': [Command.create({'product_id': self.base.id, 'qty': 2, 'price_unit': 100,
                'discount': 10, 'price_subtotal': 180, 'price_subtotal_incl': 208.80,
                'tint_color_code': self.color.code, 'tax_ids': [Command.set(tax.ids)]})]})
        pos_invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'invoice_line_ids': ticket._prepare_invoice_lines('out_invoice')})
        with patch.object(type(pos_invoice), '_l10n_mx_edi_cfdi_invoice_try_send', return_value=None):
            pos_invoice.action_post()
        ticket.write({'account_move': pos_invoice.id, 'state': 'paid'})
        self.env.flush_all()
        row = report.search([('pos_order_id', '=', ticket.id)])
        self.assertEqual(len(row), 1)
        self.assertAlmostEqual(row.sale_price, 180, places=2)
        self.assertAlmostEqual(row.invoiced_amount, 208.80, places=2)
        print('PASS: Ventas y POS, dos envases, descuento e IVA real; importe neto y total con IVA.')


result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([ReportAmounts('test_report_tax_amounts')]))
raise SystemExit(0 if result.wasSuccessful() else 1)
