"""Verify the installed native POS/Sales bridge in the local Osel database."""
import os
import sys

import odoo
from odoo import api
from odoo.orm.registry import Registry


if __name__ == '__main__':
    odoo.tools.config.parse_config([
        '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
        '--db_password=' + os.environ['PASSWORD'], '--no-http',
        '--log-handler=odoo.modules.loading:WARNING',
    ])
    with Registry('Osel').cursor() as cr:
        env = api.Environment(cr, 1, {})
        modules = env['ir.module.module'].search([('name', 'in', ['entintados_pdv', 'pos_sale'])])
        print('Modules:', [(module.name, module.state, module.installed_version) for module in modules], flush=True)
        configs = env['pos.config'].search([])
        if '--prepare-flows' in sys.argv:
            for company in configs.company_id:
                company.with_company(company).action_setup_advance_accounts()
            for config in configs:
                config.with_company(config.company_id).action_prepare_deferred_payments()
            print('Prepared PPD, authorized credit and paid customer advances.', flush=True)
        if '--configure-missing' in sys.argv:
            product = env.ref('pos_sale.default_downpayment_product')
            missing = configs.filtered(lambda config: not config.down_payment_product_id)
            missing.write({'down_payment_product_id': product.id})
            print('Configured native product on:', missing.ids, flush=True)
        print('POS:', [(config.id, config.name, config.down_payment_product_id.display_name,
                        config.company_id.advance_risk_enabled) for config in configs], flush=True)
        view = env.ref('point_of_sale.view_pos_pos_form')._get_combined_arch()
        print('Native invoice button:', bool(view.xpath('//button[@name="action_view_invoice"]')), flush=True)
        print('Sales button:', bool(view.xpath('//button[@name="action_view_sale_order"]')), flush=True)
        if '--load-session' in sys.argv:
            for config in configs:
                session = config.current_session_id
                if not session:
                    continue
                params = session.load_data_params()
                payload = session.load_data([])
                record = next(value for value in payload['pos.config'] if value['id'] == config.id)
                company = next(value for value in payload['res.company'] if value['id'] == record['company_id'])
                assert 'use_pricelist' in record
                assert 'tax_calculation_rounding_method' in company
                assert 'company_id' in params['pos.config']['relations']
                if record.get('advance_risk_enabled'):
                    assert record['advance_product_id'] in {value['id'] for value in payload['product.product']}
                if 'pos_payment_flow' in env['pos.order']._fields:
                    assert 'pos_payment_flow' in params['pos.order']['relations']
                    assert record['ppd_payment_term_id']
                    assert any(value['type'] == 'pay_later' for value in payload['pos.payment.method'])
                print('Session payload OK:', session.id, 'models:', len(payload),
                      'configuration fields:', len(record), 'company:', company['id'], flush=True)
        if '--configure-missing' in sys.argv or '--prepare-flows' in sys.argv:
            cr.commit()
