"""Real ORM checks; all schema, security and test data are rolled back."""
import test_stock_return_credit_orm as runner


def checks(env):
    import base64
    import logging
    from datetime import timedelta
    from odoo import Command, fields
    from odoo.exceptions import AccessError
    from odoo.tools.convert import convert_file

    logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)

    env.registry.init_models(env.cr, ['crm.lead'], {'module': 'entintados_pdv'}, install=False)
    for filename in ('security/demo_flow_security.xml', 'security/ir.model.access.csv',
                     'views/crm_lead_views.xml', 'views/demo_flow_menu_views.xml'):
        convert_file(env, 'entintados_pdv', filename, {}, mode='update')

    def user(role, suffix):
        return env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Demo access ' + suffix, 'login': 'demo-access-' + suffix,
            'company_id': env.company.id, 'company_ids': [Command.set(env.company.ids)],
            'group_ids': [Command.set((env.ref('base.group_user') | env.ref('entintados_pdv.group_demo_' + role)).ids)],
        })

    seller, other = user('crm', 'seller'), user('crm', 'other')
    approver, other_approver = user('approval', 'approver'), user('approval', 'other-approver')
    auditor, other_auditor = user('audit', 'auditor'), user('audit', 'other-auditor')
    employee = env['hr.employee'].create({
        'name': 'Demo audit test', 'user_id': auditor.id, 'company_id': env.company.id,
        'department_id': env.ref('entintados_pdv.hr_department_auditoria').id,
    })
    boss = env['hr.employee'].create({'name': 'Demo boss', 'user_id': approver.id, 'company_id': env.company.id})
    env['hr.employee'].create({'name': 'Demo seller', 'user_id': seller.id, 'parent_id': boss.id, 'company_id': env.company.id})
    stage = env['crm.stage'].search([('stage_type', '=', 'demo')], limit=1)
    if not stage:
        stage = env['crm.stage'].create({'name': 'Demo access test', 'stage_type': 'demo'})
    partner = env['res.partner'].create({'name': 'Demo access customer', 'phone': '5551234567'})
    location = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1).lot_stock_id
    product = env['product.product'].create({'name': 'Demo access product', 'type': 'consu', 'is_storable': True})
    env['stock.quant']._update_available_quantity(product, location, 100)
    lead = env['crm.lead'].with_user(seller).create({
        'name': 'Own demonstration', 'type': 'opportunity', 'user_id': seller.id,
        'stage_id': stage.id, 'partner_id': partner.id, 'wharehouse_id': location.id,
        'material_line_ids': [Command.create({'product_id': product.id, 'uom_id': product.uom_id.id, 'quantity': 1})],
    })
    start = fields.Datetime.now() + timedelta(days=1)
    env['calendar.event'].with_user(seller).create({
        'name': 'Access test demonstration', 'start': start, 'stop': start + timedelta(hours=1),
        'crm_lead_id': lead.id, 'crm_activity_type_id': env.ref('entintados_pdv.mail_activity_type_demo').id,
    })
    action = lead.action_request_material_output()
    assert action['res_model'] == 'crm.lead' and action['res_id'] == lead.id
    request = lead.sudo().approval_request_id
    request.material_auditor_ids = [Command.set(employee.ids)]
    request.with_user(approver).action_approve()
    picking = request.generated_picking_id
    assert picking and picking.crm_lead_id == lead.sudo()

    def denied(action):
        try:
            with env.cr.savepoint():
                action()
        except AccessError:
            return
        raise AssertionError('Expected AccessError')

    for record in (lead, request, picking):
        for actor in (seller, approver, auditor):
            record.with_user(actor).check_access('read')
        for actor in (other, other_approver, other_auditor):
            denied(lambda: record.with_user(actor).check_access('read'))
            assert not env[record._name].with_user(actor).search([('id', '=', record.id)])
    lead.write({'description': 'Seller can edit CRM'})
    request.with_user(approver).write({'reason': 'Approver can edit assigned approval'})
    picking.with_user(auditor).write({'note': 'Auditor can edit assigned output'})
    for actor in (approver, auditor):
        denied(lambda: lead.with_user(actor).write({'description': 'Forbidden'}))
    for actor in (seller, auditor):
        denied(lambda: request.with_user(actor).write({'reason': 'Forbidden'}))
        denied(lambda: request.with_user(actor).action_approve())
        denied(lambda: request.with_user(actor).action_cancel())
    for actor in (seller, approver):
        denied(lambda: picking.with_user(actor).write({'note': 'Forbidden'}))
        denied(lambda: picking.with_user(actor).action_audit_refuse_material())
        denied(lambda: picking.with_user(actor).button_validate())
    for record, actor in ((lead, seller), (request, approver), (picking, auditor)):
        denied(lambda: record.with_user(actor).unlink())
    # Direct child writes must not provide a back door across modules.
    denied(lambda: lead.material_line_ids.with_user(approver).write({'description': 'Forbidden'}))
    denied(lambda: request.product_line_ids.with_user(seller).write({'description': 'Forbidden'}))
    denied(lambda: picking.move_ids.with_user(approver).write({'product_uom_qty': 5}))
    assert 'demo_evidence_attachment_ids' not in lead._fields
    assert 'demo_use_evidence_attachment_ids' not in lead._fields
    attachment = env['ir.attachment'].with_user(auditor).create({
        'name': 'Delivery evidence.txt', 'datas': base64.b64encode(b'Delivery evidence'),
        'res_model': 'stock.picking', 'res_id': picking.id,
    })
    picking.with_user(auditor).write({'evidence_attachment_ids': [Command.link(attachment.id)]})
    assert attachment.id in picking.evidence_attachment_ids.ids
    denied(lambda: picking.with_user(seller).write({'evidence_attachment_ids': [Command.clear()]}))
    audited = picking.with_user(auditor).with_context(skip_sms=True, skip_backorder=True)
    audited.action_validate_delivery()
    audited.move_ids.write({'quantity': 1, 'picked': True})
    audited.action_audit_approve_material()
    assert picking.state == 'done'
    picking.with_user(auditor).write({'evidence_use_attachment_ids': [Command.link(attachment.id)]})
    audited.action_validate_material_use()
    assert lead.demo_end and picking.validate_material_use

    # Broad native groups must not override these global demonstration limits.
    other_approver.write({'group_ids': [Command.link(env.ref('approvals.group_approval_manager').id),
                                       Command.link(env.ref('sales_team.group_sale_manager').id),
                                       Command.link(env.ref('stock.group_stock_manager').id)]})
    for record in (lead, request, picking):
        denied(lambda: record.with_user(other_approver).check_access('read'))
    denied(lambda: request.with_user(approver).write({'crm_lead_id': False}))
    denied(lambda: picking.with_user(auditor).write({'approval_request_id': False}))
    denied(lambda: env['approval.request'].with_user(approver).create({
        'category_id': request.category_id.id, 'crm_lead_id': lead.id,
        'request_owner_id': approver.id, 'warehouse_id': location.id,
    }))

    # Native approvals and ordinary inventory operations remain editable.
    category = env['approval.category'].create({'name': 'Ordinary approval access test'})
    ordinary = env['approval.request'].with_user(approver).create({
        'name': 'Ordinary request', 'category_id': category.id, 'request_owner_id': approver.id,
    })
    ordinary.write({'reason': 'Native edit preserved'})
    stock_type = env['stock.picking.type'].search([('code', '=', 'outgoing'), ('company_id', '=', env.company.id),
                                                ('id', '!=', env.ref('entintados_pdv.picking_type_material_output').id)], limit=1)
    normal = env['stock.picking'].with_user(auditor).create({
        'picking_type_id': stock_type.id, 'location_id': location.id,
        'location_dest_id': env.ref('stock.stock_location_customers').id,
        'crm_lead_id': lead.id,
    })
    normal.write({'note': 'Native inventory edit preserved'})
    return 'PASS: own/assigned visibility, three role boundaries, CRM request and approval generation, child records, evidence upload, native approval/inventory and views.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
