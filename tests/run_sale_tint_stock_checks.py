"""Run transactional tint delivery regressions against an explicitly selected DB."""
import os
import logging
import sys
import unittest

from odoo.tools import config
from odoo.modules.registry import Registry

if __name__ == '__main__':
    database = sys.argv[1]
    config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', database, '--no-http',
                         '--db_host=' + os.environ['HOST'],
                         '--db_user=' + os.environ['USER'],
                         '--db_password=' + os.environ['PASSWORD']])
    Registry(database)
    logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
    from odoo.tests import common
    from odoo.addons.entintados_pdv.tests.test_sale_tint import TestSaleTint
    common.get_db_name = lambda: database
    names = sys.argv[2:] or [name for name in unittest.defaultTestLoader.getTestCaseNames(TestSaleTint)
             if any(part in name for part in (
                 'delivery', 'return_restores', 'two_tints', 'consumption_cannot', 'cancel_before'))]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(TestSaleTint(name) for name in names))
    raise SystemExit(0 if result.wasSuccessful() else 1)
