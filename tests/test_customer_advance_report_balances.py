"""Standalone checks of report amounts, without changing an Odoo database."""
import ast
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
import unittest


source = Path(__file__).parents[1] / 'models' / 'advance_accounting.py'
names = {
    '_advance_report_credit_lines', '_advance_report_balances',
    '_advance_report_used_amounts_by_invoice',
    '_advance_report_credit_status',
}
tree = ast.parse(source.read_text(encoding='utf-8'))
methods = [node for cls in tree.body if isinstance(cls, ast.ClassDef)
           for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
namespace = {'defaultdict': defaultdict, '_': lambda value: value}
exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)


class Lines(list):
    def filtered(self, predicate):
        return Lines(line for line in self if predicate(line))


class Advance:
    amount_total = 1000.0
    advance_paid = 1000.0

    def __init__(self, lines):
        self.lines = Lines(lines)

    def ensure_one(self):
        pass

    def _advance_application_lines(self):
        return self.lines


for name in names:
    setattr(Advance, name, namespace[name])


def application(amount, kind='out_refund', state='posted', target=1):
    invoice = SimpleNamespace(id=target, move_type='out_invoice', state='posted')
    return SimpleNamespace(
        move_id=SimpleNamespace(move_type=kind, state=state, advance_target_id=invoice),
        _advance_application_amount=lambda: amount,
    )


class TestCustomerAdvanceReportBalances(unittest.TestCase):
    def test_credit_status_follows_related_credit_note(self):
        line = application(100, state='draft')
        advance = Advance([line, application(50, kind='out_invoice')])
        self.assertEqual(advance._advance_report_credit_status(), 'Borrador')
        line.move_id.state = 'posted'
        self.assertEqual(advance._advance_report_credit_status(), 'Registrado')
        line.move_id.state = 'cancel'
        self.assertEqual(advance._advance_report_credit_status(), 'Cancelado')

    def test_pending_without_credit_notes(self):
        self.assertEqual(Advance([])._advance_report_credit_status(), 'Pendiente')
        self.assertEqual(Advance([application(50, kind='out_invoice')])._advance_report_credit_status(), 'Pendiente')

    def test_multiple_credit_notes_show_distinct_states(self):
        advance = Advance([
            application(100), application(150), application(50, state='draft'),
            application(40, state='cancel'),
        ])
        self.assertEqual(advance._advance_report_credit_status(), 'Borrador / Registrado / Cancelado')
        self.assertEqual(advance._advance_report_balances()['advance_applied'], 250)

    def test_posted_invoice_deduction_is_not_usage(self):
        advance = Advance([application(300, kind='out_invoice')])
        self.assertEqual(advance._advance_report_balances(), {
            'amount_total': 1000, 'advance_applied': 0, 'advance_available': 1000,
        })
        self.assertEqual(dict(advance._advance_report_used_amounts_by_invoice()), {})

    def test_only_posted_credit_notes_count_and_group_by_invoice(self):
        advance = Advance([
            application(100), application(150), application(50, target=2),
            application(200, state='draft'), application(300, state='cancel'),
            application(80, kind='out_invoice'), application(90, kind='in_refund'),
        ])
        self.assertEqual(advance._advance_report_balances()['advance_applied'], 300)
        self.assertEqual(advance._advance_report_balances()['advance_available'], 700)
        self.assertEqual(dict(advance._advance_report_used_amounts_by_invoice()), {1: 250, 2: 50})

    def test_cancelling_credit_note_restores_report_balance(self):
        line = application(400)
        advance = Advance([line])
        self.assertEqual(advance._advance_report_balances()['advance_available'], 600)
        line.move_id.state = 'cancel'
        self.assertEqual(advance._advance_report_balances()['advance_applied'], 0)
        self.assertEqual(advance._advance_report_balances()['advance_available'], 1000)

    def test_partial_receipt_uses_paid_amount(self):
        advance = Advance([application(100)])
        advance.advance_paid = 400
        self.assertEqual(advance._advance_report_balances()['advance_available'], 300)


if __name__ == '__main__':
    unittest.main()
