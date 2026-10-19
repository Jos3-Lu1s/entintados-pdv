"""Real SQL transaction regression; fiscal calls/config writes are mocked.

Run standalone inside the Odoo container. Only a temporary table is written.
"""
from unittest.mock import patch
from odoo import api, SUPERUSER_ID
from odoo.tools import config
from odoo.modules.registry import Registry

config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
registry = Registry('Osel_Test')
with registry.cursor() as cr:
    env = api.Environment(cr, SUPERUSER_ID, {})
    moves = env['account.move']
    params = env['ir.config_parameter']
    eligible = moves.with_context(allowed_company_ids=env.user.company_ids.ids).search([
        ('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
        ('country_code', '=', 'MX'), ('l10n_mx_edi_payment_policy', '=', 'PPD'),
        ('l10n_mx_edi_cfdi_state', '=', 'sent'),
        ('company_id', 'in', env.user.company_ids.ids),
    ], order='id')
    assert len(eligible) >= 2
    ids = eligible.ids
    cursor = {'last': '0'}
    calls = []
    cr.execute('CREATE TEMP TABLE cfdi_cron_regression (invoice_id integer) ON COMMIT PRESERVE ROWS')
    cr.commit()
    def compute(records):
        for record in records:
            record.l10n_mx_edi_update_payments_needed = True
    def update(record):
        invoice_id = record.id
        calls.append(invoice_id)
        if invoice_id == ids[0]:
            cr.execute('INSERT INTO cfdi_cron_regression VALUES (%s)', [invoice_id])
            cr.commit()  # Reproduce the commit performed by the native CFDI API.
        elif invoice_id == ids[1]:
            cr.execute('SELECT 1 / 0')  # Leave PostgreSQL in an aborted transaction.
        else:
            cr.execute('INSERT INTO cfdi_cron_regression VALUES (%s)', [invoice_id])
    def get_param(record, key, default=False):
        assert key == 'entintados_pdv.payment_cfdi_cron_last_invoice_id'
        return cursor['last']
    def set_param(record, key, value):
        assert key == 'entintados_pdv.payment_cfdi_cron_last_invoice_id'
        cr.execute('SELECT 1')  # Must work even immediately after the SQL error.
        cursor['last'] = value
    def progress(record, *args, **kwargs):
        cr.commit()
        return 60
    try:
        with patch.object(type(moves), '_compute_l10n_mx_edi_update_payments_needed', compute), \
             patch.object(type(moves), 'l10n_mx_edi_cfdi_invoice_try_update_payments', update), \
             patch.object(type(params), 'get_param', get_param), \
             patch.object(type(params), 'set_param', set_param), \
             patch.object(type(env['ir.cron']), '_commit_progress', progress):
            moves._cron_l10n_mx_edi_update_payments(batch_size=len(ids))
        assert calls == ids, (calls, ids)
        assert cursor['last'] == '0'
        cr.execute('SELECT invoice_id FROM cfdi_cron_regression ORDER BY invoice_id')
        assert [row[0] for row in cr.fetchall()] == [ids[0]] + ids[2:]
        print('PASS: native internal commit, real SQL failure, rollback recovery, remaining invoices and earlier committed result preserved. No PAC calls or persistent business writes.')
    finally:
        cr.rollback()
