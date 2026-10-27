"""Prueba ORM de evidencias; se revierte íntegramente al terminar."""
import logging

import test_stock_return_credit_orm as runner


def checks(env):
    from odoo import Command
    from odoo.exceptions import UserError
    from odoo.tools.convert import convert_file

    logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
    env.registry.init_models(env.cr, ['crm.lead', 'approval.request'],
                             {'module': 'entintados_pdv'}, install=False)
    convert_file(env, 'entintados_pdv', 'views/stock_picking_view.xml', {}, mode='update')
    company = env.company
    audit = env.ref('entintados_pdv.hr_department_auditoria')
    auxiliary = env['hr.department'].create({'name': 'Auxiliar de Auditoría', 'company_id': company.id})
    users = env['res.users'].with_context(no_reset_password=True).create([
        {'name': name, 'login': login, 'company_id': company.id,
         'company_ids': [Command.set(company.ids)]}
        for name, login in [('Auditor evidencias', 'evidence-audit-test'),
                            ('Auxiliar evidencias', 'evidence-aux-test'),
                            ('Usuario ajeno', 'evidence-other-test')]
    ])
    for user, department in zip(users[:2], audit | auxiliary):
        env['hr.employee'].create({'name': user.name, 'user_id': user.id,
                                   'department_id': department.id, 'company_id': company.id})
    warehouse = env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
    product = env['product.product'].create({'name': 'Prueba de entrega', 'type': 'consu', 'is_storable': True})
    env['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 5)
    picking = env['stock.picking'].create({
        'picking_type_id': warehouse.out_type_id.id, 'location_id': warehouse.lot_stock_id.id,
        'location_dest_id': env.ref('stock.stock_location_customers').id,
        'document_filename': 'entrega.pdf',
        'move_ids': [Command.create({'product_id': product.id, 'product_uom_qty': 1,
                                     'product_uom': product.uom_id.id})],
    })
    auditor = picking.with_user(users[0]).sudo()
    helper = picking.with_user(users[1]).sudo()
    outsider = picking.with_user(users[2]).sudo()
    assert auditor.can_validate_material_evidence and helper.can_validate_material_evidence
    assert not outsider.can_validate_material_evidence

    def rejected(action):
        try:
            with env.cr.savepoint():
                action()
        except UserError:
            return
        raise AssertionError('La operación debía rechazarse.')

    rejected(auditor.action_validate_delivery)
    picking.action_confirm()
    picking.action_assign()
    assert picking.state == 'assigned'
    rejected(helper.action_validate_delivery)
    attachment = env['ir.attachment'].create({'name': 'Evidencia.pdf', 'datas': b'ZXZpZGVuY2lh',
                                             'res_model': 'stock.picking', 'res_id': picking.id})
    picking.evidence_attachment_ids = [Command.link(attachment.id)]
    # A filename and delivery attachments do not replace the mandatory binary.
    rejected(helper.action_validate_delivery)
    rejected(lambda: helper.write({'validate_delivery': True}))
    assert not picking.validate_delivery and not picking.document_file
    picking.document_file = b'ZG9jdW1lbnRv'
    # Validate the effective new value, not a document being removed in this write.
    rejected(lambda: helper.write({'document_file': False, 'validate_delivery': True}))
    assert picking.document_file and not picking.validate_delivery
    rejected(outsider.action_validate_delivery)
    rejected(lambda: outsider.write({'validate_delivery': True}))
    rejected(lambda: picking.write({'evidence_use_attachment_ids': [Command.link(attachment.id)]}))
    assert not picking.evidence_use_attachment_ids
    helper.action_validate_delivery()
    assert picking.validate_delivery and picking.state == 'assigned'
    rejected(lambda: picking.write({'document_file': b'b3Rybw=='}))
    rejected(lambda: picking.write({'document_filename': 'otro.pdf'}))
    rejected(helper.action_validate_material_use)
    picking.move_ids.quantity = 1
    picking.with_context(skip_sms=True, skip_backorder=True).button_validate()
    assert picking.state == 'done'
    rejected(auditor.action_validate_material_use)
    picking.evidence_use_attachment_ids = [Command.link(attachment.id)]
    rejected(outsider.action_validate_material_use)
    auditor.action_validate_material_use()
    assert picking.validate_material_use
    return 'PASS: documento binario obligatorio, nombre/adjuntos insuficientes, validación por botón/RPC, eliminación simultánea bloqueada, permisos y uso en Hecho.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
