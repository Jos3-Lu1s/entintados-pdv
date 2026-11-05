"""Validate the cash cut with real reconciliation records; rollback all fixtures."""
import sys
from unittest.mock import patch

from odoo import api, Command, fields, SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tools import config


database = sys.argv[1] if len(sys.argv) > 1 else 'OSEL_UAT'
config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http'])
with Registry(database).cursor() as cr:
    env = api.Environment(cr, SUPERUSER_ID, {'mail_notify_force_send': False})
    company = env.company
    partner = company.partner_id
    today = fields.Date.context_today(env['branch.cash.cut'])
    sale_journal = env['account.journal'].search([('company_id', '=', company.id), ('type', '=', 'sale')], limit=1)
    bank = env['account.journal'].search([('company_id', '=', company.id), ('type', '=', 'bank')], limit=1)
    income = env['account.account'].search([('company_ids', 'in', company.ids), ('account_type', '=', 'income')], limit=1)
    assert sale_journal and bank and income
    outstanding = env['account.account'].create({
        'name': 'Cash cut test outstanding', 'code': 'CUTTEST01', 'account_type': 'asset_current',
        'reconcile': True, 'company_ids': [Command.set(company.ids)],
    })
    sale = env['sale.order'].create({'partner_id': partner.id})
    product = env['product.product'].create({
        'name': 'Cash cut test service', 'type': 'service', 'default_code': 'CASH-CUT-TEST',
    })
    sale_line = env['sale.order.line'].create({
        'order_id': sale.id, 'product_id': product.id, 'name': 'Cash cut test',
        'product_uom_qty': 1, 'price_unit': 100,
    })
    invoice = env['account.move'].create({
        'move_type': 'out_invoice', 'partner_id': partner.id, 'journal_id': sale_journal.id,
        'invoice_date': today, 'l10n_mx_edi_payment_policy': 'PPD',
        'invoice_line_ids': [Command.create({
            'name': 'Cash cut test', 'account_id': income.id, 'quantity': 1,
            'price_unit': 100, 'tax_ids': [Command.clear()], 'sale_line_ids': [Command.link(sale_line.id)],
        })],
    })
    with patch.object(type(invoice), '_l10n_mx_edi_cfdi_invoice_try_send', return_value=None):
        invoice.with_context(bypass_risk=True).action_post()
    receivable = invoice.line_ids.filtered(lambda line: line.account_type == 'asset_receivable')
    for amount in (20, 30):
        payment = env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': partner.id,
            'journal_id': bank.id, 'date': today, 'amount': amount,
            'force_outstanding_account_id': outstanding.id,
            'destination_account_id': receivable.account_id.id,
        })
        payment.action_post()
        (receivable | payment.move_id.line_ids.filtered(lambda line: line.account_id == receivable.account_id)).reconcile()
    cut = env['branch.cash.cut'].create({'date_from': today, 'date_to': today})
    assert cut.include_pos and cut.include_sale
    action = cut.action_open_cut()
    assert action['res_model'] == 'branch.cash.cut.line'
    header = env[action['res_model']].with_context(**action['context'])
    filters = header.action_change_filters()
    assert filters['target'] == 'new' and filters['context']['default_date_from'] == today
    assert filters['context']['default_include_pos'] and filters['context']['default_include_sale']
    assert header.action_print_detail()['report_name'] == 'entintados_pdv.branch_cash_cut_detail_pdf'
    rows = env['branch.cash.cut.line'].search([('cut_id', '=', cut.id), ('invoice_id', '=', invoice.id)])
    assert len(rows) == 3
    assert sorted(rows.filtered('payment_move_id').mapped('credit_receipts')) == [20, 30]
    assert sum(rows.mapped('net')) == 50 and sum(rows.mapped('credit_billed')) == 100
    report = env['report.entintados_pdv.branch_cash_cut_pdf']
    data = report._get_report_values(cut.ids)
    assert data['totals']['credit_receipts'] >= 50
    assert 'credit_billed' not in data['totals']
    html, _ = env['ir.actions.report']._render_qweb_html('entintados_pdv.action_branch_cash_cut_pdf', cut.ids)
    assert all(heading.encode() in html for heading in (
        'Método de pago', 'Subtipo', 'Contado cobrado', 'Abonos a crédito',
        'Anticipos recibidos', 'Devoluciones', 'Total neto',
    ))
    assert cut.action_print_cut()['report_name'] == 'entintados_pdv.branch_cash_cut_pdf'
    pdf, kind = env['ir.actions.report']._render_qweb_pdf('entintados_pdv.action_branch_cash_cut_pdf', cut.ids)
    assert kind == 'pdf' and pdf.startswith(b'%PDF')
    detail_ref = 'entintados_pdv.action_branch_cash_cut_detail_pdf'
    detail_html, _ = env['ir.actions.report']._render_qweb_html(detail_ref, cut.ids)
    assert invoice.name.encode() in detail_html and b'Subtotal neto' in detail_html
    assert b'Tipo de pago' in detail_html and 'Crédito'.encode() in detail_html
    assert b'cash_cut_payments' in detail_html and b'Contado cobrado' not in detail_html
    assert b'Cash cut test service' not in detail_html
    detail_pdf, kind = env['ir.actions.report']._render_qweb_pdf(detail_ref, cut.ids)
    assert kind == 'pdf' and detail_pdf.startswith(b'%PDF')
    cut.include_sale = False
    cut.action_open_cut()
    assert not env['branch.cash.cut.line'].search_count([('cut_id', '=', cut.id), ('invoice_id', '=', invoice.id)])
    cut.include_pos = False
    try:
        cut.action_print_cut()
    except UserError:
        pass
    else:
        raise AssertionError('Se permitió imprimir sin seleccionar orígenes')
    menu = env.ref('entintados_pdv.menu_branch_cash_cut')
    assert menu.parent_id == env.ref('entintados_pdv.menu_osel_reports')
    assert menu.parent_id.parent_id == env.ref('account.menu_finance_reports')
    cr.rollback()
    print('OK: dos abonos, crédito separado, filtro de origen, menú de Reportes y PDF con siete columnas.')
