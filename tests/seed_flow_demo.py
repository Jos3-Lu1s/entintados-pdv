"""Create a disposable, populated training database; never run on production.

Run inside the Odoo container after installing entintados_pdv and
account_check_printing. TEKUNO_DEMO_PASSWORD supplies the training password.
Stages commit independently so setup can be inspected before loading records.
"""
import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from odoo import api, Command, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.tools import config


DATABASE = 'Tekuno_Flujos_Prueba_20261028'
TODAY = date(2026, 10, 28)
PREFIX = 'training_20261028_'
LOGIN = 'd.zayas@tekuno.mx'


def remember(env, key, record):
    record.ensure_one()
    env['ir.model.data'].create({
        'module': 'entintados_pdv', 'name': PREFIX + key,
        'model': record._name, 'res_id': record.id, 'noupdate': True,
    })
    return record


def get(env, key):
    return env.ref('entintados_pdv.' + PREFIX + key)


def account(env, key, label, kind, reconcile=False):
    return remember(env, key, env['account.account'].create({
        'name': 'PRUEBA ' + label, 'code': 'P' + key.replace('_', '').upper()[:15],
        'account_type': kind, 'reconcile': reconcile,
        'company_ids': [Command.set(env.company.ids)],
    }))


def setup(env):
    params = env['ir.config_parameter'].sudo()
    if params.get_param('tekuno.training.setup'):
        print('Setup already completed', flush=True)
        return
    # Neutralize this copy before adding any business records.
    env['ir.cron'].search([]).write({'active': False})
    env['ir.mail_server'].search([]).write({'active': False})
    env['fetchmail.server'].search([]).write({'active': False})
    params.set_param('database.uuid', str(uuid4()))
    params.set_param('web.base.url', 'http://localhost:8069')
    params.set_param('web.base.url.freeze', 'True')
    params.set_param('database.is_neutralized', 'True')
    company = env.company
    company.write({
        'name': 'Tekuno Pruebas de Flujos', 'country_id': env.ref('base.mx').id,
        'currency_id': env.ref('base.MXN').id, 'account_fiscal_country_id': env.ref('base.mx').id,
        'l10n_mx_edi_pac_test_env': True, 'email': False,
        'invoice_unpaid_margin': 0, 'allow_overrisk_invoice_validation': False,
    })
    if not company.chart_template:
        env['account.chart.template'].try_loading('mx', company, install_demo=False)
    groups = env['res.groups']
    for xmlid in (
        'base.group_system', 'account.group_account_manager', 'sales_team.group_sale_manager',
        'purchase.group_purchase_manager', 'stock.group_stock_manager',
        'point_of_sale.group_pos_manager', 'approvals.group_approval_manager', 'hr.group_hr_manager',
        'entintados_pdv.group_tint_manager', 'entintados_pdv.group_account_financial_risk_manager',
        'entintados_pdv.group_sale_commercial_agreements',
    ):
        groups |= env.ref(xmlid)
    user = env['res.users'].with_context(no_reset_password=True).create({
        'name': 'Daniel Zayas Pruebas', 'login': LOGIN, 'email': False,
        'password': os.environ['TEKUNO_DEMO_PASSWORD'],
        'company_id': company.id, 'company_ids': [Command.set(company.ids)],
        'group_ids': [Command.set(groups.ids)], 'tz': 'America/Mexico_City',
    })
    remember(env, 'user', user)
    env['hr.employee'].create({
        'name': user.name, 'user_id': user.id, 'company_id': company.id,
        'department_id': env.ref('entintados_pdv.hr_department_tesoreria').id,
    })
    ar = account(env, 'receivable', 'Clientes', 'asset_receivable', True)
    ap = account(env, 'payable', 'Proveedores', 'liability_payable', True)
    income = account(env, 'income', 'Ventas de demostración', 'income')
    expense = account(env, 'expense', 'Compras de demostración', 'expense')
    incoming = account(env, 'incoming', 'Cobros por conciliar', 'asset_current', True)
    outgoing = account(env, 'outgoing', 'Pagos por conciliar', 'asset_current', True)
    bridge = account(env, 'pos_bridge', 'Cuenta puente PDV', 'asset_receivable', True)
    company.account_default_pos_receivable_account_id = bridge
    company.advance_risk_enabled = True
    for key, name, typ, code in (
        ('sales', 'PRUEBA Facturas clientes', 'sale', 'PVTA'),
        ('purchases', 'PRUEBA Facturas proveedores', 'purchase', 'PCOM'),
        ('bank', 'PRUEBA Banco y cheques', 'bank', 'PBAN'),
        ('cash', 'PRUEBA Efectivo caja', 'cash', 'PCAJ'),
        ('pos_journal', 'PRUEBA Asientos de caja', 'general', 'PPOS'),
    ):
        journal = remember(env, key, env['account.journal'].create({
            'name': name, 'type': typ, 'code': code, 'company_id': company.id,
        }))
        if typ in ('bank', 'cash'):
            journal.inbound_payment_method_line_ids.payment_account_id = incoming
            journal.outbound_payment_method_line_ids.payment_account_id = outgoing
    bank = get(env, 'bank')
    method = env.ref('account_check_printing.account_payment_method_check')
    check_line = bank.outbound_payment_method_line_ids.filtered(lambda line: line.payment_method_id == method)
    if not check_line:
        check_line = env['account.payment.method.line'].create({
            'name': 'Cheques de prueba', 'journal_id': bank.id, 'payment_method_id': method.id,
            'payment_account_id': outgoing.id,
        })
    check_line.payment_account_id = outgoing
    bank.check_manual_sequencing = True
    bank.check_sequence_id.number_next_actual = 1001
    remember(env, 'check_method', check_line)
    for key, label, values in (
        ('supplier', 'PRUEBA Proveedor notas distribuidas',
         {'is_supplier': True, 'distributed_notes': True, 'percentage_patyc': 80}),
        ('customer', 'PRUEBA Cliente conciliación', {'is_customer': True}),
        ('credit_customer', 'PRUEBA Cliente crédito excedido',
         {'is_customer': True, 'is_credit': True, 'credit_limit': 10000}),
        ('risk_customer', 'PRUEBA Cliente riesgo financiero',
         {'is_customer': True, 'is_credit': True, 'credit_limit': 15000}),
        ('cancel_customer', 'PRUEBA Cliente cancelación', {'is_customer': True}),
        ('cash_customer', 'PRUEBA Cliente caja', {'is_customer': True}),
    ):
        remember(env, key, env['res.partner'].create(dict(values,
            name=label, phone='5551234567', country_id=env.ref('base.mx').id,
            user_id=user.id, property_account_receivable_id=ar.id,
            property_account_payable_id=ap.id, risk_invoice_open_include=True,
            risk_invoice_unpaid_include=True)))
    for key, label, price, storable in (
        ('paint', 'PRUEBA Pintura blanca litro', 250, True),
        ('roller', 'PRUEBA Rodillo', 100, True),
        ('service', 'PRUEBA Servicio de demostración', 100, False),
    ):
        product = remember(env, key, env['product.product'].create({
            'name': label, 'default_code': 'DEMO-' + key.upper(),
            'type': 'consu' if storable else 'service', 'is_storable': storable,
            'list_price': price, 'standard_price': price / 2, 'available_in_pos': True,
            'property_account_income_id': income.id, 'property_account_expense_id': expense.id,
            'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
        }))
        if storable:
            warehouse = env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
            env['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 100)
    company.account_sale_tax_id = False
    company.account_purchase_tax_id = False
    cash = remember(env, 'cash_method', env['pos.payment.method'].create({
        'name': 'PRUEBA Efectivo', 'journal_id': get(env, 'cash').id,
        'company_id': company.id, 'receivable_account_id': bridge.id, 'is_cash_count': True,
    }))
    transfer = remember(env, 'transfer_method', env['pos.payment.method'].create({
        'name': 'PRUEBA Transferencia', 'journal_id': bank.id,
        'company_id': company.id, 'receivable_account_id': bridge.id, 'split_transactions': True,
        'outstanding_account_id': incoming.id,
    }))
    for key, label in (('pos', 'PRUEBA Caja para operar'), ('closed_pos', 'PRUEBA Caja cierre realizado')):
        box_cash = cash
        if key == 'closed_pos':
            box_journal = remember(env, 'closed_cash', env['account.journal'].create({
                'name': 'PRUEBA Efectivo caja cerrada', 'type': 'cash', 'code': 'PCC2',
                'company_id': company.id,
            }))
            box_journal.inbound_payment_method_line_ids.payment_account_id = incoming
            box_journal.outbound_payment_method_line_ids.payment_account_id = outgoing
            box_cash = remember(env, 'closed_cash_method', env['pos.payment.method'].create({
                'name': 'PRUEBA Efectivo caja cerrada', 'journal_id': box_journal.id,
                'company_id': company.id, 'receivable_account_id': bridge.id, 'is_cash_count': True,
            }))
        pos = remember(env, key, env['pos.config'].create({
            'name': label, 'company_id': company.id,
            'journal_id': get(env, 'pos_journal').id, 'invoice_journal_id': get(env, 'sales').id,
            'payment_method_ids': [Command.set((box_cash | transfer).ids)],
        }))
        pos.with_user(user).action_prepare_deferred_payments()
    params.set_param('tekuno.training.setup', 'done')
    env.cr.commit()
    print('SETUP OK', flush=True)


def invoice(env, key, partner, amount, supplier=False, days=0, posted=True, product=None):
    values = {
        'move_type': 'in_invoice' if supplier else 'out_invoice', 'partner_id': partner.id,
        'journal_id': get(env, 'purchases' if supplier else 'sales').id,
        'invoice_date': TODAY + timedelta(days=days - 30) if days < 0 else TODAY,
        'invoice_date_due': TODAY + timedelta(days=days),
        'invoice_payment_term_id': False, 'ref': 'PRUEBA ' + key,
        'invoice_line_ids': [Command.create({
            'name': 'PRUEBA ' + key, 'account_id': get(env, 'expense' if supplier else 'income').id,
            'product_id': (product or get(env, 'service')).id,
            'quantity': 1, 'price_unit': amount, 'tax_ids': [Command.clear()],
        })],
    }
    move = remember(env, key, env['account.move'].create(values))
    if posted:
        move.with_context(bypass_risk=True).action_post()
    return move


def payment(env, key, partner, amount, supplier=False, target=None, cheque=False):
    bank = get(env, 'bank')
    method = (get(env, 'check_method') if cheque else
              (bank.outbound_payment_method_line_ids if supplier else bank.inbound_payment_method_line_ids)
              .filtered(lambda line: line.code == 'manual')[:1])
    pay = remember(env, key, env['account.payment'].create({
        'journal_id': bank.id, 'payment_method_line_id': method.id,
        'partner_id': partner.id, 'partner_type': 'supplier' if supplier else 'customer',
        'payment_type': 'outbound' if supplier else 'inbound', 'amount': amount,
        'currency_id': env.company.currency_id.id, 'date': TODAY, 'memo': 'PRUEBA ' + key,
    }))
    pay.action_post()
    if target:
        kind = 'liability_payable' if supplier else 'asset_receivable'
        (pay.move_id | target).line_ids.filtered(
            lambda line: line.account_id.account_type == kind and not line.reconciled).reconcile()
    return pay


def pos_order(env, key, session, partner, amount, methods=None, draft=False):
    cash = session.config_id.payment_method_ids.filtered('is_cash_count')[:1]
    methods = methods or [(cash, amount)]
    order = remember(env, key, env['pos.order'].with_user(get(env, 'user')).create({
        'session_id': session.id, 'partner_id': partner.id, 'uuid': str(uuid4()),
        'amount_total': amount, 'amount_tax': 0, 'amount_paid': 0 if draft else amount,
        'amount_return': 0, 'date_order': '2026-10-28 16:00:00',
        'lines': [Command.create({'product_id': get(env, 'service').id, 'qty': 1,
            'price_unit': amount, 'price_subtotal': amount, 'price_subtotal_incl': amount})],
        'payment_ids': [] if draft else [Command.create({'payment_method_id': m.id, 'amount': a}) for m, a in methods],
    }))
    if not draft:
        order.action_pos_order_paid()
    return order


def records(env):
    params = env['ir.config_parameter'].sudo()
    if params.get_param('tekuno.training.records'):
        print('Records already completed', flush=True)
        return
    assert params.get_param('tekuno.training.setup'), 'Run setup first'
    if not get(env, 'closed_cash_method').journal_id:
        journal = remember(env, 'closed_cash', env['account.journal'].create({
            'name': 'PRUEBA Efectivo caja cerrada', 'type': 'cash', 'code': 'PCC2',
            'company_id': env.company.id,
        }))
        journal.inbound_payment_method_line_ids.payment_account_id = get(env, 'incoming')
        journal.outbound_payment_method_line_ids.payment_account_id = get(env, 'outgoing')
        get(env, 'closed_cash_method').journal_id = journal
    bridge = env.ref('entintados_pdv.' + PREFIX + 'pos_bridge', raise_if_not_found=False)
    if not bridge:
        bridge = account(env, 'pos_bridge', 'Cuenta puente PDV', 'asset_receivable', True)
    env.company.account_default_pos_receivable_account_id = bridge
    (get(env, 'cash_method') | get(env, 'closed_cash_method') | get(env, 'transfer_method')).receivable_account_id = bridge
    get(env, 'transfer_method').outstanding_account_id = get(env, 'incoming')
    # The module auto-sends sale-linked invoices. Suppress the transport only
    # while preparing training records; retain the genuine accounting methods.
    with patch.object(type(env['account.move']), '_l10n_mx_edi_cfdi_invoice_try_send', return_value=None):
        supplier = get(env, 'supplier')
        for idx, total in enumerate((10000, 20000), start=1):
            bill = invoice(env, 'vendor_pending_' + str(idx), supplier, total, supplier=True)
            payment(env, 'vendor_partial_' + str(idx), supplier, total * .8, supplier=True, target=bill)
        pending = remember(env, 'distribution_pending', env['vendor.credit.group'].create({
            'partner_id': supplier.id, 'reason': 'PRUEBA A Generar notas de 2000 y 4000', 'supplier_reference': 'DEMO-DISTRIBUCION-A',
        }))
        pending._populate_lines_from_partner()
        examples = env['account.move']
        for idx, total in enumerate((5000, 7500), start=1):
            bill = invoice(env, 'vendor_example_' + str(idx), supplier, total, supplier=True)
            payment(env, 'vendor_paid_' + str(idx), supplier, total * .8, supplier=True, target=bill)
            examples |= bill
        group = remember(env, 'distribution_done', env['vendor.credit.group'].create({
            'partner_id': supplier.id, 'reason': 'PRUEBA B Ejemplo generado y conciliado', 'supplier_reference': 'DEMO-DISTRIBUCION-B',
            'line_ids': [Command.create({'invoice_id': bill.id, 'amount': bill.amount_residual}) for bill in examples],
        }))
        group.with_user(get(env, 'user')).action_create_credit()
        customer = get(env, 'customer')
        inv = invoice(env, 'customer_partial', customer, 10000, days=30)
        payment(env, 'customer_payment_partial', customer, 4000, target=inv)
        invoice(env, 'customer_unpaid', customer, 5000, days=30)
        pay = payment(env, 'customer_unallocated', customer, 2500)
        remember(env, 'statement_pending', env['account.bank.statement.line'].create({
            'journal_id': get(env, 'bank').id, 'date': TODAY,
            'payment_ref': 'PRUEBA Conciliar ingreso de 2500', 'amount': 2500,
            'partner_id': customer.id,
        }))
        # Invoice reconciliation and bank reconciliation are separate steps.
        matched = payment(env, 'customer_payment_matched', customer, 1200,
                          target=invoice(env, 'customer_fully_paid', customer, 1200))
        statement = remember(env, 'statement_matched', env['account.bank.statement.line'].create({
            'journal_id': get(env, 'bank').id, 'date': TODAY,
            'payment_ref': 'PRUEBA Ingreso ya conciliado de 1200', 'amount': 1200,
            'partner_id': customer.id,
        }))
        liquidity, suspense, other = statement._seek_for_lines()
        suspense.account_id = get(env, 'incoming')
        (suspense | matched.move_id.line_ids.filtered(
            lambda line: line.account_id == get(env, 'incoming'))).reconcile()
        invoice(env, 'risk_current', get(env, 'risk_customer'), 6000, days=30)
        invoice(env, 'risk_overdue', get(env, 'risk_customer'), 3000, days=-15)
        invoice(env, 'risk_draft', get(env, 'risk_customer'), 2000, posted=False)
        invoice(env, 'credit_existing_debt', get(env, 'credit_customer'), 8000, days=30)
        # Two outgoing cheque examples, with genuinely assigned consecutive numbers.
        cheque_bill = invoice(env, 'cheque_bill', supplier, 3000, supplier=True)
        payment(env, 'cheque_issued', supplier, 3000, supplier=True, target=cheque_bill, cheque=True)
        remember(env, 'cheque_draft', env['account.payment'].create({
            'partner_id': supplier.id, 'partner_type': 'supplier', 'payment_type': 'outbound',
            'amount': 1500, 'journal_id': get(env, 'bank').id,
            'payment_method_line_id': get(env, 'check_method').id,
            'date': TODAY, 'memo': 'PRUEBA Cheque pendiente de confirmar',
        }))
        # A delivered sales invoice is ready for a real test-PAC cancellation later.
        sale = remember(env, 'cancel_sale', env['sale.order'].create({
            'partner_id': get(env, 'cancel_customer').id,
            'order_line': [Command.create({'product_id': get(env, 'paint').id,
                'product_uom_qty': 4, 'price_unit': 250, 'tax_ids': [Command.clear()]})],
        }))
        sale.action_confirm()
        for picking in sale.picking_ids:
            picking.action_assign()
            picking.move_ids.picked = True
            picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
        cancel_invoice = sale._create_invoices()
        if cancel_invoice.state == 'draft':
            cancel_invoice.invoice_date = TODAY
            cancel_invoice.with_context(bypass_risk=True).action_post()
        remember(env, 'cancel_invoice', cancel_invoice)
        draft = invoice(env, 'cancel_draft', get(env, 'cancel_customer'), 500, posted=False)
        simple = invoice(env, 'cancel_accounting', get(env, 'cancel_customer'), 700)
        # Keep one live session for hands-on testing and one genuinely closed session.
        for key, poskey in (('open_session', 'pos'), ('closed_session', 'closed_pos')):
            session = remember(env, key, env['pos.session'].with_user(get(env, 'user')).create({
                'config_id': get(env, poskey).id, 'user_id': get(env, 'user').id,
            }))
            session.with_user(get(env, 'user')).set_opening_control(1000, 'PRUEBA Fondo inicial')
            pos_order(env, key + '_cash_sale', session, get(env, 'cash_customer'), 500)
            pos_order(env, key + '_transfer_sale', session, get(env, 'cash_customer'), 750,
                      [(get(env, 'transfer_method'), 750)])
            pos_order(env, key + '_mixed_sale', session, get(env, 'cash_customer'), 1000,
                      [(session.config_id.payment_method_ids.filtered('is_cash_count')[:1], 400),
                       (get(env, 'transfer_method'), 600)])
            if key == 'closed_session':
                session.cash_register_balance_end_real = session.cash_register_balance_end
                session.with_user(get(env, 'user')).action_pos_session_closing_control()
                assert session.state == 'closed', session.state
        session = get(env, 'open_session')
        for key, amount, decision in (('credit_pending', 4000, None),
                                      ('credit_approved', 3500, 'action_approve'),
                                      ('credit_refused', 3000, 'action_refuse')):
            order = pos_order(env, key + '_order', session, get(env, 'credit_customer'), amount, draft=True)
            response = get(env, 'pos').with_user(get(env, 'user')).request_credit_approval(
                order.partner_id.id, order.uuid, amount, amount, 0, order.id)
            approval = remember(env, key, env['pos.credit.approval'].browse(response['id']))
            if decision:
                getattr(approval.approval_request_id.with_user(get(env, 'user')), decision)()
        params.set_param('tekuno.training.records', 'done')
        env.cr.commit()
    print('RECORDS OK', flush=True)


def finish(env):
    """Finish the training delivery without the optional native SMS wizard."""
    for picking in get(env, 'cancel_sale').picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')):
        picking.action_assign()
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
        assert picking.state == 'done'
    env.cr.commit()
    print('DELIVERY FINISHED', flush=True)


def verify(env):
    assert get(env, 'user').active
    assert get(env, 'distribution_pending').amount_total == 6000
    assert get(env, 'distribution_done').credit_count == 2
    assert all(bill.amount_residual == 0 for bill in get(env, 'distribution_done').line_ids.invoice_id)
    assert get(env, 'customer_partial').amount_residual == 6000
    assert get(env, 'customer_unpaid').amount_residual == 5000
    assert get(env, 'customer_payment_matched').is_matched
    assert get(env, 'statement_matched').is_reconciled
    assert get(env, 'credit_pending').state == 'pending'
    assert get(env, 'credit_approved').state == 'approved'
    assert get(env, 'credit_refused').state == 'refused'
    assert get(env, 'closed_session').state == 'closed'
    assert get(env, 'open_session').state == 'opened'
    assert int(get(env, 'cheque_issued').check_number) == 1001
    assert get(env, 'cancel_invoice').state == 'posted'
    assert all(p.state == 'done' for p in get(env, 'cancel_sale').picking_ids)
    report = env['report.point_of_sale.report_saledetails'].with_user(get(env, 'user')).get_sale_details(
        session_ids=get(env, 'closed_session').ids)
    for group in report['invoice_payment_groups']:
        assert group['currency'].is_zero(sum(row['amount'] for row in group['rows']) - group['total'])
    assert sum(g['total'] for g in report['invoice_payment_groups']) == 2250
    keys = env['ir.model.data'].search([('module', '=', 'entintados_pdv'), ('name', '=like', PREFIX + '%')])
    data = {'database': DATABASE, 'login': LOGIN, 'date': str(TODAY), 'records': {}}
    for reference in keys:
        record = env[reference.model].browse(reference.res_id).exists()
        if record:
            data['records'][reference.name.removeprefix(PREFIX)] = {
                'model': reference.model, 'id': record.id, 'name': record.display_name,
                **({'state': record.state} if 'state' in record._fields else {}),
                **({'residual': record.amount_residual} if 'amount_residual' in record._fields else {}),
            }
    risk = get(env, 'risk_customer')
    data['risk'] = {name: risk[name] for name in ('credit_limit', 'risk_total', 'risk_invoice_open',
        'risk_invoice_unpaid', 'risk_remaining_value', 'risk_invoice_draft')}
    data['report'] = [{k: g[k] for k in ('name', 'total')} for g in report['invoice_payment_groups']]
    data['checks'] = {'number': get(env, 'cheque_issued').check_number,
                      'layout': env.company.account_check_printing_layout}
    Path('/tmp/tekuno_flow_demo_inventory.json').write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({'verified': True, 'records': len(data['records']), 'risk': data['risk'],
                      'report': data['report'], 'checks': data['checks']}, ensure_ascii=False), flush=True)


def exercise(env):
    """Exercise the eight topics, then undo every change to training records."""
    from odoo.exceptions import UserError
    results = []
    user = get(env, 'user')

    def run(label, check):
        # A dedicated savepoint preserves the initial state for every case.
        class UndoScenario(Exception):
            pass
        try:
            with env.cr.savepoint():
                check()
                raise UndoScenario()
        except UndoScenario:
            results.append({'topic': label, 'result': 'PASS'})
            print('PASS ' + label, flush=True)

    def distribution():
        group = get(env, 'distribution_pending').with_user(user)
        invoices = group.line_ids.invoice_id
        group.action_create_credit()
        assert group.credit_count == 2 and sum(group.credit_ids.mapped('amount_total')) == 6000
        assert sum(invoices.mapped('amount_residual')) == 0
        original = group.credit_ids
        group.action_create_credit()
        assert group.credit_ids == original
        group.action_cancel()
        assert sum(invoices.mapped('amount_residual')) == 6000
        group.action_draft()
        assert group.state == 'draft'
    run('Nota de crédito distribuida', distribution)

    def reconcile():
        target = get(env, 'customer_unpaid').with_user(user)
        pay = get(env, 'customer_unallocated').with_user(user)
        lines = (target | pay.move_id).line_ids.filtered(
            lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled)
        lines.reconcile()
        assert target.amount_residual == 2500
        statement = get(env, 'statement_pending').with_user(user)
        _, suspense, _ = statement._seek_for_lines()
        suspense.account_id = get(env, 'incoming')
        (suspense | pay.move_id.line_ids.filtered(
            lambda line: line.account_id == get(env, 'incoming'))).reconcile()
        assert statement.is_reconciled and pay.is_matched
    run('Conciliación contable y bancaria', reconcile)

    def cancellation():
        move = get(env, 'cancel_accounting').with_user(user)
        move.button_draft()
        move.button_cancel()
        assert move.state == 'cancel'
        invoice = get(env, 'cancel_invoice')
        invoice._entintados_return_cancelled_invoice_products()
        returns = invoice.cancellation_return_ids
        assert returns and all(p.state == 'done' for p in returns)
        assert sum(returns.move_ids.mapped('quantity')) == 4
        invoice._entintados_return_cancelled_invoice_products()
        assert invoice.cancellation_return_ids == returns
        # Exercise the test-PAC callback without inventing a stamped CFDI.
        doc = env['l10n_mx_edi.document'].new({'move_id': invoice.id, 'state': 'invoice_sent'})
        calls = []
        def no_transport(*args, **kwargs):
            raise AssertionError('Unexpected fiscal transport')
        with patch.object(type(doc), '_can_commit', return_value=False), \
             patch.object(type(doc), '_get_pac_method_map', no_transport):
            doc._cancel_api(env.company, '03', no_transport, lambda: calls.append(True))
        assert calls == [True]
    run('Cancelación contable motor de inventario y simulación PAC', cancellation)

    def credit():
        request = get(env, 'credit_pending')
        request.approval_request_id.with_user(user).action_approve()
        assert request.state == 'approved'
        order = request.order_id.with_user(user)
        deferred = order.config_id.payment_method_ids.filtered(lambda method: method.type == 'pay_later')[:1]
        order.write({'to_invoice': True, 'pos_payment_flow': 'credit', 'amount_paid': 4000,
                     'payment_ids': [Command.create({'payment_method_id': deferred.id, 'amount': 4000})]})
        order._check_pos_deferred_payments(check_risk=True)
        assert request.state == 'used' and request.used_order_id == order
        order._check_pos_deferred_payments(check_risk=True)
    run('Autorización y consumo único de crédito', credit)

    def risk():
        partner = get(env, 'risk_customer').with_user(user)
        assert partner.risk_total == 9000 and partner.risk_remaining_value == 6000
        partner.risk_invoice_draft_include = True
        assert partner.risk_total == 11000 and partner.risk_remaining_value == 4000
    run('Parámetros de riesgo financiero', risk)

    def checks():
        pay = get(env, 'cheque_draft').with_user(user)
        pay.action_post()
        assert int(pay.check_number) == 1002 and pay.move_id.state == 'posted'
        assert pay.check_amount_in_words
    run('Emisión y numeración de cheque sin impresión bancaria', checks)

    def closing():
        session = get(env, 'open_session').with_user(user)
        # Draft requests intentionally stay in the open box. The technical
        # close test cancels them inside this rolled-back transaction.
        drafts = env['pos.order'].search([('session_id', '=', session.id), ('state', '=', 'draft')])
        drafts.write({'state': 'cancel'})
        assert session.cash_register_balance_end == 1900
        session.cash_register_balance_end_real = 1900
        session.action_pos_session_closing_control()
        assert session.state == 'closed' and session.move_id.state == 'posted'
    run('Corte y contabilización de caja', closing)

    def reports():
        session = get(env, 'closed_session')
        report = env['report.point_of_sale.report_saledetails'].with_user(user)
        groups = report.get_sale_details(session_ids=session.ids)['invoice_payment_groups']
        assert sorted(g['total'] for g in groups) == [900, 1350]
        for group in groups:
            assert group['currency'].is_zero(sum(r['amount'] for r in group['rows']) - group['total'])
        html, _ = env['ir.actions.report'].with_user(user)._render_qweb_html(
            'point_of_sale.report_saledetails', session.ids)
        assert b'invoice_payments_detail' in html
    run('Reporte de pagos por método y generación HTML', reports)
    env.cr.rollback()
    Path('/tmp/tekuno_flow_exercise_results.json').write_text(
        json.dumps({'database': DATABASE, 'rollback': True, 'checks': results}, indent=2, ensure_ascii=False))
    print('ALL EIGHT TOPICS PASS; TRAINING RECORDS PRESERVED', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('setup', 'records', 'finish', 'verify', 'exercise'))
    args = parser.parse_args()
    config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', DATABASE, '--no-http',
                         '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
                         '--db_password=' + os.environ['PASSWORD']])
    assert config['db_name'] == [DATABASE] or config['db_name'] == DATABASE
    registry = Registry(DATABASE)
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {
            'mail_notify_force_send': False, 'mail_auto_subscribe_no_notify': True,
            'tracking_disable': True, 'tz': 'America/Mexico_City',
        })
        globals()[args.stage](env)
