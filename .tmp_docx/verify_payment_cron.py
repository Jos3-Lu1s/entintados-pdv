"""Exercise cron pagination on the real ORM, without fiscal calls or commits."""
from unittest.mock import patch
from pathlib import Path
from lxml import etree
from odoo import api, SUPERUSER_ID
from odoo.tools import config
from odoo.modules.registry import Registry

config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
registry = Registry('Osel_Test')
with registry.cursor() as cr:
    try:
        env = api.Environment(cr, SUPERUSER_ID, {})
        moves = env['account.move']
        key = 'entintados_pdv.payment_cfdi_cron_last_invoice_id'
        params = env['ir.config_parameter'].sudo()
        params.set_param(key, '0')
        eligible = moves.with_context(allowed_company_ids=env.user.company_ids.ids).search([
            ('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
            ('country_code', '=', 'MX'), ('l10n_mx_edi_payment_policy', '=', 'PPD'),
            ('l10n_mx_edi_cfdi_state', '=', 'sent'),
            ('company_id', 'in', env.user.company_ids.ids),
        ], order='id')
        assert len(eligible) >= 2, 'Need at least two eligible invoices'
        calls = []
        progress = []
        def compute(records):
            for record in records:
                record.l10n_mx_edi_update_payments_needed = True
        def update(record):
            calls.append(record.id)
            if record.id == eligible[0].id:
                raise ValueError('Simulated invoice failure')
        def commit(record, processed=0, **kwargs):
            progress.append((processed, kwargs))
            return 60
        with patch.object(type(moves), '_compute_l10n_mx_edi_update_payments_needed', compute), \
             patch.object(type(moves), 'l10n_mx_edi_cfdi_invoice_try_update_payments', update), \
             patch.object(type(env['ir.cron']), '_commit_progress', commit):
            for index, invoice in enumerate(eligible):
                moves._cron_l10n_mx_edi_update_payments(batch_size=1)
                assert calls[-1] == invoice.id
                expected = str(invoice.id) if index + 1 < len(eligible) else '0'
                assert params.get_param(key) == expected
            assert calls == eligible.ids, (calls, eligible.ids)
            assert progress[-1] == (1, {'remaining': 0})
            calls.clear()
            def no_pending(records):
                for record in records:
                    record.l10n_mx_edi_update_payments_needed = False
            with patch.object(type(moves), '_compute_l10n_mx_edi_update_payments_needed', no_pending):
                moves._cron_l10n_mx_edi_update_payments()
            assert not calls, 'Unchanged invoices should not invoke fiscal updates'
            params.set_param(key, str(eligible[-1].id))
            moves._cron_l10n_mx_edi_update_payments()
            assert params.get_param(key) == '0'
            calls.clear()
            params.set_param(key, '0')
            with patch.object(type(env['ir.cron']), '_commit_progress', return_value=0):
                moves._cron_l10n_mx_edi_update_payments()
            assert not calls, 'Expired scheduler budget should stop processing'
        xml = Path(__file__).resolve().parents[1] / 'data/account_payment_cfdi_cron.xml'
        tree = etree.parse(str(xml))
        assert tree.xpath("string(//field[@name='code'])") == 'model._cron_l10n_mx_edi_update_payments()'
        print('PASS: ORM domain, pagination, failure isolation, cursor reset, unchanged invoices, time budget and XML. Fiscal calls and commits mocked; rollback enforced.')
    finally:
        cr.rollback()
