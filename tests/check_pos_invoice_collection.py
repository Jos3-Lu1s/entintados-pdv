"""End-to-end invoice collection regression checks, always rolled back; no PAC calls."""
import logging
from unittest.mock import patch

from odoo import api, Command, fields, SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tools import config

config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
with Registry('Osel_Test').cursor() as cr:
    env = api.Environment(cr, SUPERUSER_ID, {'mail_notify_force_send': False})
    company = env.company
    company.advance_risk_enabled = False
    def account(code, kind, reconcile=False):
        return env['account.account'].create({
            'name': 'Collection test ' + code, 'code': 'COLL' + code,
            'account_type': kind, 'reconcile': reconcile, 'company_ids': [Command.set(company.ids)],
        })
    receivable = account('REC', 'asset_receivable', True)
    interim = account('POS', 'asset_receivable', True)
    income = account('INC', 'income')
    company.account_default_pos_receivable_account_id = interim
    partner = env['res.partner'].create({
        'name': 'Cliente prueba abonos PPD', 'phone': '5551234567', 'is_customer': True,
        'property_account_receivable_id': receivable.id,
    })
    sales = env['account.journal'].search([('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1)
    bank = env['account.journal'].create({'name': 'Collection test bank', 'code': 'CTB', 'type': 'bank', 'company_id': company.id})
    method = env['pos.payment.method'].create({'name': 'Collection test transfer', 'journal_id': bank.id, 'company_id': company.id})
    deferred = env['pos.payment.method'].create({'name': 'Collection test debt', 'journal_id': False, 'company_id': company.id, 'split_transactions': True})
    source_config = env['pos.config'].search([('company_id', '=', company.id)], limit=1)
    pos = source_config.copy({'name': 'Collection test POS', 'cash_control': False,
                              'payment_method_ids': [Command.set((method | deferred).ids)]})
    session = env['pos.session'].create({'config_id': pos.id})
    session.action_pos_session_open()
    def invoice(amount):
        move = env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id, 'journal_id': sales.id,
            'invoice_date': fields.Date.today(), 'l10n_mx_edi_payment_policy': 'PPD',
            'invoice_line_ids': [Command.create({'name': 'Venta original', 'account_id': income.id,
                                               'quantity': 1, 'price_unit': amount, 'tax_ids': [Command.clear()]})],
        })
        move.with_context(bypass_risk=True).action_post()
        return move
    def ticket(invoices, amounts):
        order = env['pos.order'].create({
            'session_id': session.id, 'partner_id': partner.id, 'to_invoice': True,
            'amount_tax': 0, 'amount_total': 0, 'amount_paid': 0, 'amount_return': 0,
            'lines': [Command.create({'product_id': pos.settle_invoice_product_id.id,
                                      'full_product_name': inv.name, 'settled_invoice_id': inv.id,
                                      'qty': 0, 'price_unit': amount, 'price_subtotal': 0,
                                      'price_subtotal_incl': 0, 'tax_ids': [Command.clear()]})
                      for inv, amount in zip(invoices, amounts)],
            'payment_ids': [Command.create({'payment_method_id': method.id, 'amount': sum(amounts)}),
                            Command.create({'payment_method_id': deferred.id, 'amount': -sum(amounts)})],
        })
        return order
    assert not partner.get_advance_pos_info(pos.id)['has_outstanding_invoices']
    first, second = invoice(1000), invoice(600)
    assert partner.get_advance_pos_info(pos.id)['has_outstanding_invoices']
    invoice_count = env['account.move'].search_count([('move_type', '=', 'out_invoice')])
    order = ticket([first], [300])
    order._process_saved_order(False)
    assert order.invoice_collection_processed and order.state == 'done' and not order.account_move and not order.to_invoice
    assert env['account.move'].search_count([('move_type', '=', 'out_invoice')]) == invoice_count
    assert first.amount_residual == 700 and first.pos_amount_unsettled == 700
    payment = order.invoice_collection_payment_ids
    assert len(payment) == 1 and payment.amount == 300 and payment.move_id.state == 'posted'
    assert first in payment.reconciled_invoice_ids
    assert payment.move_id._l10n_mx_edi_is_cfdi_payment()
    assert not order.l10n_mx_edi_is_cfdi_needed
    order._register_invoice_collection()
    assert len(order.invoice_collection_payment_ids) == 1
    multi = ticket([first, second], [200, 250])
    multi._process_saved_order(False)
    assert first.amount_residual == 500 and second.amount_residual == 350
    assert len(multi.invoice_collection_payment_ids) == 2
    assert sorted(multi.invoice_collection_payment_ids.mapped('amount')) == [200, 250]
    values = env['report.point_of_sale.report_saledetails'].get_sale_details(session_ids=session.ids)
    collected = [group for group in values['invoice_payment_groups'] if group['name'] == method.name]
    assert len(collected) == 1 and collected[0]['total'] == 750
    totals = {row['invoice']: row['amount'] for row in collected[0]['rows']}
    assert totals[first.name] == 500 and totals[second.name] == 250
    assert not any(group['credit'] for group in values['invoice_payment_groups'])
    cash_journal = env['account.journal'].create({'name': 'Collection test cash', 'code': 'CTC', 'type': 'cash', 'company_id': company.id})
    cash_method = env['pos.payment.method'].create({'name': 'Collection test cash', 'journal_id': cash_journal.id,
                                                  'company_id': company.id, 'split_transactions': True})
    pos.with_context(bypass_payment_method_ids_forbidden_change=True).write({'payment_method_ids': [Command.link(cash_method.id)]})
    cash_order = ticket([first], [100])
    cash_line = cash_order.payment_ids.filtered(lambda item: item.payment_method_id == method)
    cash_line.write({'payment_method_id': cash_method.id, 'amount': 150})
    env['pos.payment'].create({'pos_order_id': cash_order.id, 'payment_method_id': cash_method.id,
                               'amount': -50, 'is_change': True})
    cash_order._process_saved_order(False)
    assert cash_order.invoice_collection_payment_ids.amount == 100
    assert first.amount_residual == 400
    values = env['report.point_of_sale.report_saledetails'].get_sale_details(session_ids=session.ids)
    cash_group = next(group for group in values['invoice_payment_groups'] if group['name'] == cash_method.name)
    assert cash_group['total'] == 100 and sum(row['amount'] for row in cash_group['rows']) == 100
    over = ticket([second], [351])
    try:
        with cr.savepoint():
            over._process_saved_order(False)
    except UserError:
        pass
    else:
        raise AssertionError('Se aceptó un abono superior al saldo')
    over.action_pos_order_cancel()
    # Closing must transfer the bridge balance to the actual bank only once.
    session.action_pos_session_closing_control()
    assert session.state == 'closed', session.state
    bridge = (order | multi | cash_order).invoice_collection_payment_ids.move_id.line_ids.filtered(lambda line: line.account_id == interim)
    assert all(line.reconciled for line in bridge)
    session_bridge = session.move_id.line_ids.filtered(lambda line: line.account_id == interim and line.balance)
    assert all(line.reconciled for line in session_bridge), 'Quedaron partidas abiertas en la cuenta puente de la caja'
    assert first.amount_residual == 400 and second.amount_residual == 350
    assert env['account.move'].search_count([('move_type', '=', 'out_invoice')]) == invoice_count
    cr.rollback()
    print('OK: abono parcial y múltiple, sin factura nueva, saldo correcto, pago fiscal, reintento sin duplicados, reporte exacto, exceso rechazado y cierre de caja conciliado. Sin llamadas al PAC ni cambios persistentes.')
