"""Comprobación transaccional de presentación CRM y mensajes de seguimiento."""
import logging

import test_stock_return_credit_orm as runner


def checks(env):
    from lxml import etree
    from odoo.tools.convert import convert_file
    from odoo.addons.entintados_pdv.models.material_chatter import field_label

    logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
    env.registry.init_models(env.cr, ['crm.lead', 'crm.material.line', 'approval.request'],
                             {'module': 'entintados_pdv'}, install=False)
    for filename in ('views/crm_lead_views.xml', 'views/crm_opportunity_list_views.xml'):
        convert_file(env, 'entintados_pdv', filename, {}, mode='update')
    arch = etree.fromstring(env['crm.lead'].get_view(
        view_id=env.ref('crm.crm_lead_view_form').id, view_type='form')['arch'])
    for name in ('action_schedule_field_visit', 'action_schedule_demo_meeting'):
        buttons = arch.xpath("//header/button[@name='%s']" % name)
        assert buttons and all('not active and probability == 0' in b.get('invisible', '') for b in buttons)
    won = arch.xpath("//header/button[@name='action_set_won_rainbowman']")
    assert won and all(b.get('invisible') == '1' for b in won)
    lead = env['crm.lead'].with_context(lang='en_US').create({
        'name': 'Prueba de mensajes en español', 'type': 'lead',
    })
    product = env['product.product'].create({'name': 'Producto de prueba de bitácora'})
    before = lead.message_ids
    line = env['crm.material.line'].with_context(tracking_disable=False, lang='en_US').create({
        'lead_id': lead.id, 'product_id': product.id, 'uom_id': product.uom_id.id,
    })
    notes = lead.message_ids - before
    assert notes and 'Producto' in notes.body and 'Unidad de medida' in notes.body
    assert 'Product:' not in notes.body
    before = lead.message_ids
    line.write({'quantity': 3})
    assert 'Cantidad' in (lead.message_ids - before).body
    assert field_label(env['stock.move'], 'product_uom_qty') == 'Cantidad solicitada'
    assert field_label(env['approval.product.line'], 'description') == 'Descripción'
    lead = lead.with_context(tracking_disable=False)
    lead.write({'name': 'Seguimiento nativo en español'})
    lead._message_track(['name'], {lead.id: {'name': 'Nombre anterior'}})
    tracked = lead.message_ids.tracking_value_ids.filtered(lambda row: row.field_id.name == 'name')
    assert tracked, 'Debe conservar el seguimiento nativo.'
    assert env['crm.lead']._fields['opportunity_relations'].string == 'Aprobaciones'
    print('PASS: vistas, botones, encabezado y mensajes nuevos del chatter en español; seguimiento nativo conservado.')
    return 'PASS: cambios de presentación CRM.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
