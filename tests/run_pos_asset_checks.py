"""Compile the production POS bundle and verify the tint refund dependencies."""
import re

import test_stock_return_credit_orm as runner


def checks(env):
    bundle = env['ir.qweb']._get_asset_bundle('point_of_sale.assets_prod', css=False, js=True)
    attachment = bundle.js()
    content = attachment.raw.decode()
    definitions = set(re.findall(r'''odoo.define\(['"]([^'"]+)''', content))
    requirements = set(re.findall(r'''require\(['"]([^'"]+)''', content))
    missing = sorted(name for name in requirements - definitions if name.startswith('@entintados_pdv/'))
    assert not missing, 'Undefined POS dependencies: ' + ', '.join(missing)
    assert '@point_of_sale/app/screens/ticket_screen/ticket_screen' in definitions
    assert '@entintados_pdv/app/overrides/pos_order_line' in definitions
    assert '@entintados_pdv/app/utils/tint_order' in definitions
    assert 'Reembolsos deshabilitados' in content, 'The POS refund block is missing from the bundle'
    assert '_syncTintRefund' not in content, 'The previous refund selection patch is still active'
    assert '@entintados_pdv/app/utils/tint_refund' not in requirements
    from pathlib import Path
    from lxml import etree
    from odoo.exceptions import UserError
    base = Path('/usr/lib/python3/dist-packages/odoo/addons/point_of_sale/static/src/app/screens')
    for path, expression in [
        ('ticket_screen/ticket_screen.xml', "//ActionpadWidget[@actionToTrigger.bind='onClickRefund']"),
        ('product_screen/control_buttons/control_buttons.xml', "//button[contains(@t-on-click, 'clickRefund')]"),
    ]:
        assert etree.parse(str(base / path)).xpath(expression), 'Refund button XPath no longer matches native POS'
    env['pos.order'].new({'amount_total': 10})._check_pos_refunds_disabled()
    for action in [
        lambda: env['pos.order']._process_order({'is_refund': True}, False),
        lambda: env['pos.order']._process_order({'lines': [(0, 0, {'refunded_orderline_id': 1})]}, False),
        lambda: env['pos.order'].new({'amount_total': -10})._check_pos_refunds_disabled(),
    ]:
        try:
            action()
        except UserError:
            pass
        else:
            raise AssertionError('POS refund must be blocked by the server')
    return 'PASS: production POS bundle compiles, all dependencies defined, refund buttons match native templates, and refund submissions are rejected by the server.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
