"""Native SAT status queries and test PAC cancellation without forced statuses."""
import ast
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import MagicMock


class TestSatStatusSimulation(TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'models/l10n_mx_edi_document.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        model = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        base = type('Base', (), {
            '_fetch_sat_status': lambda record, *args: record.real_fetch(*args),
            '_cancel_api': lambda record, *args: record.real_cancel(*args),
        })
        model.bases = [ast.Name(id='Base', ctx=ast.Load())]
        model.body = [node for node in model.body if isinstance(node, ast.FunctionDef)]
        namespace = {'Base': base, '_': lambda text, *args, **kwargs: text % (args or kwargs),
                     '_logger': MagicMock()}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[model], type_ignores=[])),
                     str(source), 'exec'), namespace)
        cls = namespace['L10nMxEdiDocument']
        cls.__len__ = lambda record: 1
        self.doc = cls()
        self.doc.id = 1
        self.doc.state = 'invoice_sent'
        self.doc.sat_state = 'not_found'
        self.doc.attachment_id = MagicMock(id=10)
        self.doc.move_id = MagicMock()
        self.record = MagicMock()
        self.doc.move_id.__iter__.return_value = [self.record]
        self.root = MagicMock(l10n_mx_edi_pac_test_env=True)
        self.doc._get_company_cfdi_values = MagicMock(return_value={'root_company': self.root})
        self.doc.env = {'l10n_mx_edi.document': MagicMock()}
        self.doc.env['l10n_mx_edi.document'].search_count.return_value = 0
        self.doc.real_fetch = MagicMock(return_value={'value': 'real_service'})
        self.doc.real_cancel = MagicMock()
        self.doc.ensure_one = MagicMock()
        self.doc._can_commit = MagicMock(return_value=False)
        self.doc.attachment_uuid = 'TEST-UUID'

    def fetch(self):
        return self.doc._fetch_sat_status('EMISOR', 'RECEPTOR', 116, 'TEST-UUID')

    def test_status_queries_use_native_service_in_both_environments(self):
        for test_env in (True, False):
            self.root.l10n_mx_edi_pac_test_env = test_env
            for state in ('invoice_sent', 'invoice_cancel', 'payment_sent', 'payment_cancel'):
                self.doc.state = state
                self.assertEqual(self.fetch(), {'value': 'real_service'})
        self.record.message_post.assert_not_called()

    def test_test_cancellation_does_not_force_sat_status(self):
        success, failure = MagicMock(), MagicMock()
        self.doc._cancel_api(self.doc.move_id.company_id, '02', failure, success)
        success.assert_called_once()
        failure.assert_not_called()
        self.doc.real_cancel.assert_not_called()
        self.doc.env['l10n_mx_edi.document'].search.assert_not_called()
        self.assertEqual(self.doc.sat_state, 'not_found')

    def test_production_cancellation_uses_pac(self):
        self.root.l10n_mx_edi_pac_test_env = False
        success, failure = MagicMock(), MagicMock()
        company = self.doc.move_id.company_id
        self.doc._cancel_api(company, '02', failure, success)
        self.doc.real_cancel.assert_called_once_with(company, '02', failure, success)
        success.assert_not_called()


if __name__ == '__main__':
    main()
