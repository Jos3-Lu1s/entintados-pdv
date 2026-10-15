"""Run isolated advance/credit tests inside the existing local Odoo container."""
import os
import subprocess
import sys
from pathlib import Path


if __name__ == '__main__':
    root = Path('/usr/lib/python3/dist-packages/odoo/addons/point_of_sale/static/src')
    checks = (
        'app/components/popups/number_popup/number_popup.js',
        'app/components/popups/selection_popup/selection_popup.js',
        'app/utils/make_awaitable_dialog.js',
    )
    for relative in checks:
        if not (root / relative).is_file():
            raise SystemExit('Missing native POS import: ' + relative)
    accounting = root / 'app/models/accounting/pos_order_line_accounting.js'
    if accounting.exists():
        content = accounting.read_text()
        print('POS prices implementation:', content[content.index('get prices()'):content.index('get prices()') + 500], flush=True)
        for line in content.splitlines():
            if 'total_included_currency' in line or 'get prices' in line:
                print('POS price API:', line.strip(), flush=True)
    if '--inspect-pos' in sys.argv:
        store = (root / 'app/services/pos_store.js').read_text()
        start = store.index('async addLineToCurrentOrder')
        print(store[start:start + 700], flush=True)
        start = store.index('async addLineToOrder')
        print(store[start:start + 6000], flush=True)
        raise SystemExit(0)
    args = [
        'odoo', '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
        '--db_password=' + os.environ['PASSWORD'], '-d', 'codex_vendor_credit_test_20261013',
        '-u', 'entintados_pdv', '--test-enable',
        '--test-tags', '/entintados_pdv:TestAdvanceAccounting,/entintados_pdv:TestVendorCreditGroup',
        '--without-demo=True', '--stop-after-init', '--http-port=8079', '--max-cron-threads=0',
        '--log-handler=odoo.modules.loading:WARNING',
    ]
    raise SystemExit(subprocess.call(args))
