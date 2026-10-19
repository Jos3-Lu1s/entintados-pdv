"""Isolated flow tests; run with python tests/test_cfdi_payment_cancel.py.

The PAC and ORM are mocked so these checks cannot cancel real fiscal documents.
"""
import ast
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import MagicMock


class TestCfdiPaymentCancel(TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'models' / 'account_move.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        model = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        method = next(node for node in model.body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == '_l10n_mx_edi_cfdi_invoice_try_cancel')
        base = type('Base', (), {
            '_l10n_mx_edi_cfdi_invoice_try_cancel':
                lambda obj, doc, reason: obj.invoice_cancel(doc, reason),
        })
        model.bases = [ast.Name(id='Base', ctx=ast.Load())]
        model.body = [method]
        namespace = {'Base': base, '_': lambda text, **kw: text % kw}
        isolated = ast.fix_missing_locations(ast.Module(body=[model], type_ignores=[]))
        exec(compile(isolated, str(source), 'exec'), namespace)
        self.move = namespace['AccountMove']()
        for name in ('ensure_one', 'invoice_cancel',
                     '_l10n_mx_edi_cfdi_invoice_document_cancel_failed'):
            setattr(self.move, name, MagicMock())
        self.move.state = 'posted'
        self.move.move_type = 'out_invoice'
        self.move.l10n_mx_edi_cfdi_state = 'sent'
        self.move.l10n_mx_edi_payment_policy = 'PPD'
        self.move.ids = [1]
        self.move.company_id = MagicMock(id=7)
        self.documents = MagicMock()
        self.move.env = {
            'l10n_mx_edi.document': MagicMock(), 'res.company': MagicMock(),
        }
        self.move.env['l10n_mx_edi.document'].search.return_value = self.documents
        self.move.env['l10n_mx_edi.document'].search_count.return_value = 0
        # Recordset union is only relevant to lock acquisition here.
        namespace['AccountMove'].__or__ = lambda obj, other: other
        self.invoice_document = MagicMock()

    def run_cancel(self, *documents):
        for index, doc in enumerate(documents):
            doc.move_id.display_name = 'Pago %s' % index
        self.documents.__iter__.return_value = iter(documents)
        self.move._l10n_mx_edi_cfdi_invoice_try_cancel(self.invoice_document, '03')

    def test_cancels_all_payments_before_invoice(self):
        events = []
        payments = [MagicMock(), MagicMock()]
        self.move.env['l10n_mx_edi.document'].search_count.side_effect = [0, 1, 0, 1]
        for index, doc in enumerate(payments):
            doc.move_id._l10n_mx_edi_cfdi_invoice_try_cancel_payment.side_effect = (
                lambda document, index=index: events.append(index)
            )
        self.move.invoice_cancel.side_effect = lambda *args: events.append('invoice')
        self.run_cancel(*payments)
        self.assertEqual(events, [0, 1, 'invoice'])
        self.move.env['l10n_mx_edi.document'].search.assert_called_once_with([
            ('invoice_ids', 'in', [1]), ('state', '=', 'payment_sent'),
            ('move_id.company_id', '=', 7),
        ])

    def test_failure_preserves_invoice_and_records_error(self):
        doc = MagicMock()
        self.run_cancel(doc)
        self.move.invoice_cancel.assert_not_called()
        self.move._l10n_mx_edi_cfdi_invoice_document_cancel_failed.assert_called_once()

    def test_retry_skips_already_cancelled_payment(self):
        doc = MagicMock()
        self.move.env['l10n_mx_edi.document'].search_count.return_value = 1
        self.run_cancel(doc)
        doc.move_id._l10n_mx_edi_cfdi_invoice_try_cancel_payment.assert_not_called()
        self.move.invoice_cancel.assert_called_once_with(self.invoice_document, '03')

    def test_invoice_without_payments(self):
        self.run_cancel()
        self.move.invoice_cancel.assert_called_once()

    def test_failure_does_not_skip_remaining_payments(self):
        payments = [MagicMock(), MagicMock(), MagicMock()]
        self.move.env['l10n_mx_edi.document'].search_count.side_effect = [0, 0, 0, 1, 0, 1]
        self.run_cancel(*payments)
        for doc in payments:
            doc.move_id._l10n_mx_edi_cfdi_invoice_try_cancel_payment.assert_called_once_with(doc)
        self.move.invoice_cancel.assert_not_called()

    def test_cancellation_check_uses_original_attachment(self):
        doc = MagicMock()
        doc.attachment_id.id = 840
        doc.move_id.id = 209
        self.move.env['l10n_mx_edi.document'].search_count.side_effect = [0, 1]
        doc.move_id._l10n_mx_edi_cfdi_invoice_try_cancel_payment.side_effect = (
            lambda source: setattr(source.attachment_id, 'id', False)
        )
        self.run_cancel(doc)
        expected = [('state', '=', 'payment_cancel'), ('move_id', '=', 209),
                    ('attachment_id', '=', 840)]
        for call in self.move.env['l10n_mx_edi.document'].search_count.call_args_list:
            self.assertEqual(call.args[0], expected)
        self.move.invoice_cancel.assert_called_once()

    def test_other_invoice_flows_do_not_cancel_payments(self):
        for field, value in (
            ('l10n_mx_edi_payment_policy', 'PUE'), ('state', 'draft'),
            ('l10n_mx_edi_cfdi_state', 'cancel'), ('move_type', 'in_invoice'),
        ):
            with self.subTest(field=field):
                self.setUp()
                setattr(self.move, field, value)
                self.run_cancel(MagicMock())
                self.move.env['l10n_mx_edi.document'].search.assert_not_called()
                self.move.invoice_cancel.assert_called_once()


if __name__ == '__main__':
    main()
