"""Verifica la agrupación SQL por vendedor sin conservar datos de prueba."""
import test_stock_return_credit_orm as runner


def checks(env):
    from odoo.tools.convert import convert_file
    from odoo.tools.safe_eval import safe_eval

    env.registry.init_models(env.cr, ['crm.lead', 'approval.request'],
                             {'module': 'entintados_pdv'}, install=False)
    convert_file(env, 'entintados_pdv', 'data/crm_approval_sequence.xml', {}, mode='update')
    lead = env['crm.lead'].create({'name': 'Agrupador vendedor demo', 'type': 'lead', 'user_id': env.uid})
    request = env['approval.request'].create({
        'category_id': env.ref('entintados_pdv.approval_category_salida_material').id,
        'request_owner_id': env.uid, 'crm_lead_id': lead.id,
    })
    picking = env['stock.picking'].create({
        'picking_type_id': env.ref('entintados_pdv.picking_type_material_output').id,
        'approval_request_id': request.id, 'crm_lead_id': lead.id,
        'location_id': env.ref('stock.stock_location_stock').id,
        'location_dest_id': env.ref('stock.stock_location_customers').id,
    })
    for record in (request, picking):
        groups = record._read_group([('id', '=', record.id)], ['demo_salesperson_id'], ['__count'])
        assert len(groups) == 1 and groups[0][0] == env.user and groups[0][1] == 1
    for filename, xmlid in [
        ('views/stock_demo_list_views.xml', 'action_stock_demo_outputs'),
        ('views/approval_demo_list_views.xml', 'action_approval_demo_list'),
    ]:
        convert_file(env, 'entintados_pdv', filename, {}, mode='update')
        action = env.ref('entintados_pdv.' + xmlid)
        assert safe_eval(action.context)['search_default_group_by_demo_salesperson'] == 1
    return 'PASS: agrupación SQL por vendedor en ambas listas y activación predeterminada removible.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
