"""Create the approved 1000 / 150 / 850 example only in the training database."""
import json
import os

from odoo import api, Command, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.tools import config

from seed_flow_demo import DATABASE, TODAY, get, remember


def seed(env):
    existing = env.ref('entintados_pdv.training_20261028_manual_vendor_adjustment', raise_if_not_found=False)
    if existing and existing.exists():
        if existing.name.startswith('NCD-'):
            existing.name = 'PRUEBA-AJUSTE-NOTA-001'
        return existing
    supplier = get(env, 'supplier')
    bill = env['account.move'].create({
        'move_type': 'in_invoice', 'partner_id': supplier.id,
        'journal_id': get(env, 'purchases').id, 'invoice_date': TODAY,
        'ref': 'PRUEBA Ajuste manual de nota de proveedor',
        'invoice_line_ids': [Command.create({
            'name': 'PRUEBA Ajuste de nota y pago', 'product_id': get(env, 'service').id,
            'account_id': get(env, 'expense').id, 'quantity': 1, 'price_unit': 1000,
            'tax_ids': [Command.clear()],
        })],
    })
    bill.action_post()
    bank = get(env, 'bank')
    payment = env['account.payment'].create({
        'partner_id': supplier.id, 'partner_type': 'supplier', 'payment_type': 'outbound',
        'journal_id': bank.id, 'date': TODAY, 'amount': 800,
        'currency_id': bill.currency_id.id,
        'payment_method_line_id': bank.outbound_payment_method_line_ids.filtered(lambda m: m.code == 'manual')[:1].id,
        'memo': 'PRUEBA Pago ajustable de nota de proveedor',
    })
    payment.action_post()
    (bill | payment.move_id).line_ids.filtered(lambda l: l.account_type == 'liability_payable').reconcile()
    group = env['vendor.credit.group'].create({
        'name': 'PRUEBA-AJUSTE-NOTA-001', 'supplier_reference': 'DEMO-AJUSTE-001',
        'partner_id': supplier.id, 'journal_id': get(env, 'purchases').id,
        'reason_id': env.ref('entintados_pdv.vendor_credit_reason_discount').id,
        'date': TODAY, 'currency_id': bill.currency_id.id,
        'line_ids': [Command.create({'invoice_id': bill.id, 'amount': 200})],
    })
    group.action_create_credit()
    wizard = env['vendor.credit.group.adjustment'].with_context(default_group_id=group.id).create({
        'explanation': 'Demostración autorizada: nota de 200 a 150 y pago de 800 a 850.',
        'line_ids': [Command.create({'source_line_id': group.line_ids.id, 'amount': 150})],
    })
    wizard.action_apply()
    assert group.credit_ids.amount_total == 150 and payment.amount == 850 and bill.amount_residual == 0
    remember(env, 'manual_vendor_adjustment', group)
    return group


if __name__ == '__main__':
    config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', DATABASE, '--no-http',
        '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
        '--db_password=' + os.environ['PASSWORD']])
    registry = Registry(DATABASE)
    with registry.cursor() as cr:
        root = api.Environment(cr, SUPERUSER_ID, {})
        root['vendor.credit.group']._migrate_reason_catalog()
        user = get(root, 'user')
        env = api.Environment(cr, user.id, {'tz': 'America/Mexico_City', 'mail_notify_force_send': False,
                                         'mail_create_nosubscribe': True})
        group = seed(env)
        cr.commit()
        print(json.dumps({'group': group.name, 'id': group.id,
                          'bill': group.line_ids.invoice_id.name,
                          'credit': group.credit_ids.amount_total,
                          'payment': group.line_ids.payment_id.amount,
                          'residual': group.line_ids.invoice_id.amount_residual}, ensure_ascii=False), flush=True)
