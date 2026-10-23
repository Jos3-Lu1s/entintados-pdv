"""Verificación de la lista de demostraciones y de las validaciones de evidencias."""
import test_stock_return_credit_orm as runner
from run_stock_evidence_checks import checks as evidence_checks


def checks(env):
    from lxml import etree
    from odoo import Command
    from odoo.tools.convert import convert_file
    from odoo.tools.safe_eval import safe_eval

    print(evidence_checks(env))
    convert_file(env, 'entintados_pdv', 'data/crm_approval_sequence.xml', {}, mode='update')
    convert_file(env, 'entintados_pdv', 'views/stock_demo_list_views.xml', {}, mode='update')
    lead = env['crm.lead'].create({'name': 'Oportunidad para lista de demostraciones', 'type': 'lead'})
    auditor = env['hr.employee'].search([('user_id.login', '=', 'evidence-aux-test')], limit=1)
    request = env['approval.request'].create({
        'category_id': env.ref('entintados_pdv.approval_category_salida_material').id,
        'request_owner_id': env.uid, 'crm_lead_id': lead.id,
        'material_auditor_ids': [Command.set(auditor.ids)],
    })
    picking_type = env.ref('entintados_pdv.picking_type_material_output')
    picking = env['stock.picking'].create({
        'picking_type_id': picking_type.id, 'approval_request_id': request.id,
        'crm_lead_id': lead.id, 'location_id': env.ref('stock.stock_location_stock').id,
        'location_dest_id': env.ref('stock.stock_location_customers').id,
    })
    assert picking.demo_auditor_ids == auditor
    assert picking.demo_relations == {'opportunity': 1, 'approval': 1}
    for key, target in [('opportunity', lead), ('approval', request)]:
        action = picking.action_open_demo_relation(key)
        assert action['res_model'] == target._name and action['res_id'] == target.id
        assert action['views'] == [(False, 'form')]
    action = env.ref('entintados_pdv.action_stock_demo_outputs')
    domain = safe_eval(action.domain)
    assert picking in env['stock.picking'].search(domain)
    assert all(row.picking_type_id == picking_type for row in env['stock.picking'].search(domain))
    assert env.ref('entintados_pdv.menu_stock_demo_outputs').parent_id == env.ref('stock.menu_stock_transfers')
    view = env['stock.picking'].get_view(view_id=action.view_id.id, view_type='list')
    arch = etree.fromstring(view['arch'])
    assert [node.get('name') for node in arch.xpath('./field')] == [
        'name', 'location_id', 'location_dest_id', 'partner_id', 'origin', 'state',
        'demo_auditor_ids', 'demo_salesperson_id', 'demo_relations']
    assert not arch.xpath('.//field[@name="scheduled_date"]')
    return 'PASS: menú, lista exclusiva de demostraciones, columnas, auditores, relaciones y uso solo en Hecho.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
