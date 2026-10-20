"""Native SAT queries remain inherited; test cancellation does not force status.

Standalone check on Osel_Test, without network calls or committed writes.
"""
from unittest.mock import patch
from odoo import api, SUPERUSER_ID
from odoo.tools import config
from odoo.modules.registry import Registry


def run():
    config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
    registry = Registry('Osel_Test')
    with registry.cursor() as cr:
        try:
            env = api.Environment(cr, SUPERUSER_ID, {})
            docs = env['l10n_mx_edi.document']
            assert docs._fetch_sat_status.__module__ == 'odoo.addons.l10n_mx_edi.models.l10n_mx_edi_document'
            sample = docs.search([('state', '=', 'invoice_sent'), ('move_id', '!=', False)], limit=1)
            assert sample
            root = docs._get_company_cfdi_values(sample.move_id.company_id)['root_company']
            root.l10n_mx_edi_pac_test_env = True
            before = sample.sat_state
            calls = []
            def no_pac(*args, **kwargs):
                raise AssertionError('Unexpected PAC call')
            with patch.object(type(docs), '_can_commit', lambda record: False), \
                 patch.object(type(docs), '_get_pac_method_map', no_pac):
                sample._cancel_api(sample.move_id.company_id, '02', no_pac, lambda: calls.append(True))
            assert calls == [True]
            assert sample.sat_state == before
            print('PASS: native SAT query inherited; test cancellation callback preserved; SAT status not overwritten. No network calls; rollback enforced.')
        finally:
            cr.rollback()


if __name__ == '__main__':
    run()