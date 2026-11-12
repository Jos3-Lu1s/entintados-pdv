"""Check catalog data and views on Odoo without committing changes."""
from pathlib import Path

exec(Path(__file__).with_name('check_customer_payment_policy.py').read_text().split('@common.tagged')[0])
from odoo import api, SUPERUSER_ID
from odoo.tools.convert import convert_file

with Registry(database).cursor() as cr:
    try:
        env = api.Environment(cr, SUPERUSER_ID, {})
        env.registry.init_models(cr, ['tint.category', 'product.category', 'product.template',
                                     'tint.color.formula', 'tint.sales.report', 'tint.category.sales.report'],
                                 {'module': 'entintados_pdv'}, install=False)
        for filename in ('security/ir.model.access.csv', 'data/tint_category_data.xml',
                         'views/tint_category_views.xml', 'views/product_template_tint_views.xml',
                         'views/tint_color_formula_views.xml', 'views/tint_menu_views.xml',
                         'views/tint_sales_report_views.xml', 'report/tint_sales_report.xml'):
            convert_file(env, 'entintados_pdv', filename, {}, mode='update')
        root = env.ref('entintados_pdv.product_category_tints')
        for family in ('vinyl', 'acrylic', 'enamel'):
            category = env.ref(f'entintados_pdv.tint_category_{family}')
            product_category = env.ref(f'entintados_pdv.product_category_tint_{family}')
            assert product_category.parent_id == root
            assert product_category in category.product_category_ids
            product = env['product.template'].new({'name': 'Pintura QA', 'categ_id': product_category.id})
            assert product.tint_category_id == category
        source = env['tint.sales.report']
        report = env['tint.category.sales.report']
        candidates = source.search([('status', 'in', ['pending', 'invoiced']),
                                    ('color', 'not in', [False, '', 'Sin identificar'])], limit=1)
        if candidates:
            candidates.base_id.product_tmpl_id.categ_id = env.ref('entintados_pdv.product_category_tint_vinyl')
            cr.flush()
            assert report.browse(candidates.id).tint_category_id == env.ref('entintados_pdv.tint_category_vinyl')
            assert report.browse(candidates.id).quantity == candidates.quantity
        assert env.ref('entintados_pdv.action_tint_sales_by_category').res_model == report._name
        assert not report.search_count([('status', '=', 'quotation')])
        html, _ = env['ir.actions.report']._render_qweb_html(
            'entintados_pdv.action_tint_category_sales_pdf', report.search([], limit=1).ids)
        assert b'Ventas de entintados por' in html
        print('PASS: category hierarchy, links, product classification and Odoo views')
    finally:
        cr.rollback()
