"""Comprobación transaccional de las listas de aprobaciones CRM."""
import test_stock_return_credit_orm as runner
from run_stock_demo_list_checks import checks as stock_checks


def checks(env):
    from lxml import etree
    from odoo import Command
    from odoo.tools.convert import convert_file
    from odoo.tools.safe_eval import safe_eval

    print(stock_checks(env))
    convert_file(env, 'entintados_pdv', 'views/approval_demo_list_views.xml', {}, mode='update')
    request = env['approval.request'].search([
        ('crm_lead_id.name', '=', 'Oportunidad para lista de demostraciones')], limit=1)
    approver = env['res.users'].search([('login', '=', 'evidence-audit-test')], limit=1)
    request.approver_ids = [Command.create({'user_id': approver.id, 'required': False})]
    assert request.demo_assigned_user_ids == approver
    assert request.demo_salesperson_id == request.crm_lead_id.user_id
    picking = request.picking_ids
    assert picking.demo_salesperson_id == request.crm_lead_id.user_id
    assert request.demo_relations == {'opportunity': 1, 'pickings': 1}
    for relation, record in [('opportunity', request.crm_lead_id), ('pickings', picking)]:
        action = request.action_open_approval_demo_relation(relation)
        assert action['res_model'] == record._name and action['res_id'] == record.id
        assert action['views'] == [(False, 'form')]
    assert request in env['approval.request'].search([('approver_ids.user_id', '=', approver.id)])
    assert request not in env['approval.request'].search([('approver_ids.user_id', '=', env.uid)])
    action = env.ref('entintados_pdv.action_approval_demo_list')
    assert request in env['approval.request'].search(safe_eval(action.domain))
    assert safe_eval(action.context)['search_default_my_demo_approvals'] == 1
    assert env.ref('entintados_pdv.menu_approval_demo_list').parent_id == env.ref('approvals.approvals_menu_manager')
    arch = etree.fromstring(env['approval.request'].get_view(
        view_id=action.view_id.id, view_type='list')['arch'])
    names = [node.get('name') for node in arch.xpath('./field')]
    assert 'demo_assigned_user_ids' in names and 'demo_salesperson_id' in names and 'demo_relations' in names
    return 'PASS: lista de Gerente, asignados, vendedor CRM en ambas listas, relaciones y filtro Mis aprobaciones.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
