"""Run credit/advance accounting regressions with transactional rollback."""
import unittest

from odoo.tools import config
from odoo.modules.registry import Registry


if __name__ == '__main__':
    config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
    Registry('Osel_Test')
    from odoo.tests import common
    from odoo.addons.entintados_pdv.tests.test_advance_accounting import TestAdvanceAccounting

    common.get_db_name = lambda: 'Osel_Test'
    names = [
        'test_customer_advance_only_reduces_debt_when_explicitly_applied',
        'test_unused_advance_does_not_offset_credit_debt_or_financing',
        'test_supplier_advance_does_not_increase_customer_credit',
    ]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(
        TestAdvanceAccounting(name) for name in names))
    raise SystemExit(0 if result.wasSuccessful() else 1)
