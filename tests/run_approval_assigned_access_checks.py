"""Verificación transaccional del perfil de aprobador asignado."""
import test_stock_return_credit_orm as runner


def checks(env):
    import logging
    from lxml import etree
    from odoo import Command
    from odoo.exceptions import AccessError
    from odoo.tools.convert import convert_file

    logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)

    env.registry.init_models(env.cr, ['crm.lead', 'approval.request'],
                             {'module': 'entintados_pdv'}, install=False)
    convert_file(env, 'entintados_pdv', 'security/security.xml', {}, mode='update')
    convert_file(env, 'entintados_pdv', 'views/stock_demo_list_views.xml', {}, mode='update')
    profile = env.ref('entintados_pdv.group_approval_assigned_only')
    users = env['res.users'].with_context(no_reset_password=True).create([
        {'name': name, 'login': login, 'company_id': env.company.id,
         'company_ids': [Command.set(env.company.ids)],
         'group_ids': [Command.set((env.ref('base.group_user') | group).ids)]}
        for name, login, group in [
            ('Aprobador limitado', 'assigned-approval-test', profile),
            ('Otro aprobador', 'other-approval-test', env.ref('approvals.group_approval_user')),
            ('Administrador de aprobaciones', 'manager-approval-test', env.ref('approvals.group_approval_manager')),
        ]])
    limited, other, manager = users
    category = env['approval.category'].create({'name': 'Prueba de perfil asignado', 'approval_minimum': 1})

    def request_for(user):
        request = env['approval.request'].create({
            'name': 'Solicitud para ' + user.name, 'category_id': category.id,
            'request_owner_id': manager.id,
            'approver_ids': [Command.create({'user_id': user.id, 'required': False})],
        })
        request.action_confirm()
        return request

    mine, theirs = request_for(limited), request_for(other)
    actor = env['approval.request'].with_user(limited)
    assert len(actor.search([('id', 'in', (mine | theirs).ids)])) == 2

    def denied(action):
        try:
            with env.cr.savepoint():
                action()
        except AccessError:
            return
        raise AssertionError('La operación debía rechazarse para este perfil.')

    denied(lambda: theirs.with_user(limited).action_approve())
    denied(lambda: theirs.approver_ids.with_user(limited).action_approve())
    denied(lambda: theirs.with_user(limited).write({'name': 'Cambio no permitido'}))
    denied(lambda: mine.with_user(limited).write({'name': 'No editar datos'}))
    denied(lambda: theirs.approver_ids.with_user(limited).write({'user_id': limited.id}))
    denied(lambda: actor.create({'name': 'No crear', 'category_id': category.id}))
    denied(lambda: mine.with_user(limited).unlink())
    denied(lambda: mine.with_user(limited).action_cancel())
    denied(lambda: theirs.with_user(limited).action_confirm())
    denied(lambda: mine.with_user(limited)._action_force_approval())
    mine.with_user(limited).action_approve()
    assert mine.request_status == 'approved'
    assert theirs.request_status == 'pending'
    manager_request = env['approval.request'].with_user(manager).create({
        'name': 'Administrador mantiene permisos', 'category_id': category.id,
        'request_owner_id': manager.id,
    })
    manager_request.write({'name': 'Administrador puede editar sus borradores'})
    arch = etree.fromstring(actor.get_view(
        view_id=env.ref('approvals.approval_request_view_form').id, view_type='form')['arch'])
    assert arch.get('edit') == '0' and arch.get('create') == '0'
    action = env.ref('entintados_pdv.action_stock_demo_outputs')
    search = etree.fromstring(env['stock.picking'].get_view(
        view_id=action.search_view_id.id, view_type='search')['arch'])
    assert search.xpath('.//field[@name="demo_salesperson_id"]')
    assert search.xpath('.//filter[@name="my_demo_sales"]')
    return 'PASS: filtro por vendedor, lectura de otras solicitudes, aprobación propia, bloqueos y permisos del administrador conservados.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
