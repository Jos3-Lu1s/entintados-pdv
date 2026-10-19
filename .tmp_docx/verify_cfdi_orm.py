"""Regression on Osel_Test with PAC mocked and unconditional rollback."""
from unittest.mock import patch
import odoo
from odoo import api, SUPERUSER_ID
from odoo.tools import config
from odoo.modules.registry import Registry

config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
registry = Registry('Osel_Test')
with registry.cursor() as cr:
    try:
        env = api.Environment(cr, SUPERUSER_ID, {})
        invoice = env['account.move'].search([('name', '=', 'INV/2026/00072')])
        assert len(invoice) == 1
        docs = env['l10n_mx_edi.document']
        source = docs.search([('move_id', '=', invoice.id), ('state', '=', 'invoice_sent')], limit=1)
        sent = docs.search([('invoice_ids', 'in', invoice.ids), ('state', '=', 'payment_sent')])
        assert len(sent) == 2, sent.ids
        calls = []
        def fake_cancel(doc, company, reason, on_failure, on_success):
            calls.append((doc.id, doc.state))
            on_success()
        with patch.object(type(docs), '_cancel_api', fake_cancel):
            invoice._l10n_mx_edi_cfdi_invoice_try_cancel(source, '02')
        for doc in sent:
            assert docs.search_count([('state', '=', 'payment_cancel'),
                                      ('attachment_id', '=', doc.attachment_id.id)])
        assert calls == [(83, 'payment_sent'), (82, 'invoice_sent')], calls
        assert docs.search_count([('move_id', '=', invoice.id), ('state', 'in', ['invoice_cancel', 'invoice_cancel_requested'])])
        print('PASS ORM: pending payment cancelled, previous cancellation skipped, invoice processed last. PAC mocked; rollback enforced.')
    finally:
        cr.rollback()
