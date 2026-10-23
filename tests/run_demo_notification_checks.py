"""Pruebas transaccionales del correo de actividades de demostración."""
import test_stock_return_credit_orm as runner


def checks(env):
    from contextlib import ExitStack
    from unittest.mock import patch
    from lxml import etree
    from odoo import fields
    from odoo.tools import html2plaintext
    from odoo.tools.convert import convert_file
    from run_approval_demo_list_checks import checks as list_checks

    with ExitStack() as stack:
        stack.enter_context(patch.object(type(env['mail.mail']), 'send',
                                        side_effect=AssertionError('La prueba no debe enviar correos.')))
        convert_file(env, 'entintados_pdv', 'data/demo_activity_mail_template.xml', {}, mode='update')
        convert_file(env, 'entintados_pdv', 'data/hr_departament_data.xml', {}, mode='update')
        print(list_checks(env))
        convert_file(env, 'entintados_pdv', 'views/crm_lead_views.xml', {}, mode='update')
        convert_file(env, 'entintados_pdv', 'wizards/crm_material_auditor_assign_wizard_views.xml', {}, mode='update')
        assert env['crm.lead']._fields['user_id'].readonly
        arch = etree.fromstring(env['crm.lead'].get_view(
            view_id=env.ref('crm.crm_lead_view_form').id, view_type='form')['arch'])
        assert all(node.get('readonly') in (None, '1', 'True')
                   for node in arch.xpath('.//field[@name="user_id"]')), [
                       etree.tostring(node).decode() for node in arch.xpath('.//field[@name="user_id"]')]
        auxiliary = env.ref('entintados_pdv.hr_department_auditor_auxiliar')
        assert auxiliary.name == 'Auditor Auxiliar'
        request = env['approval.request'].search([
            ('crm_lead_id.name', '=', 'Oportunidad para lista de demostraciones')], limit=1)
        wizard = env['crm.material.auditor.assign.wizard'].create({'approval_request_id': request.id})
        assert auxiliary in wizard.auditor_department_ids, (auxiliary.ids, wizard.auditor_department_ids.ids)
        approver = request.approver_ids.user_id
        approver.email = 'aprobador.demo@example.invalid'
        request.material_auditor_ids.user_id.email = 'auditor.demo@example.invalid'
        request.write({'partner_id': approver.partner_id.id, 'date': fields.Date.today()})
        before = env['mail.mail'].search([])
        request.action_confirm()
        queued = env['mail.mail'].search([]) - before
        custom = queued.filtered(lambda mail: 'Demo pendiente de aprobación:' in (mail.subject or ''))
        assert len(custom) == 1, 'Debe generar un correo para la actividad nativa de aprobación.'
        text = html2plaintext(custom.body_html)
        assert request.name in text and request.crm_lead_id.user_id.name in text
        assert approver.name in text and 'Contacto u Oportunidad:' in text
        assert custom.state == 'outgoing', custom.read(['state', 'failure_reason', 'email_to', 'recipient_ids'])
        assert (approver.partner_id in custom.recipient_ids or approver.email in (custom.email_to or '')), custom.read(['email_to', 'recipient_ids'])
        picking = request.picking_ids
        before = env['mail.mail'].search([])
        request._notify_auditoria_department(picking)
        queued = env['mail.mail'].search([]) - before
        custom = queued.filtered(lambda mail: 'Demo pendiente de validación:' in (mail.subject or ''))
        assert len(custom) == 1
        assert picking.name in html2plaintext(custom.body_html)
        recipient = request.material_auditor_ids.user_id
        assert recipient.partner_id in custom.recipient_ids or recipient.email in (custom.email_to or ''), custom.read(['email_to', 'recipient_ids'])
        assert custom.state == 'outgoing'
        # Actividades ajenas al flujo no deben disparar esta plantilla.
        before = env['mail.mail'].search([])
        request.activity_schedule('mail.mail_activity_data_todo', user_id=approver.id)
        assert not (env['mail.mail'].search([]) - before).filtered(
            lambda mail: (mail.subject or '').startswith('Demo pendiente de'))
    return 'PASS: columnas separadas, vendedor de solo lectura, Auditor Auxiliar y correos en cola para ambas actividades; sin envíos reales.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
