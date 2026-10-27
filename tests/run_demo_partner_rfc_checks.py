"""RFC uniqueness and optional demo-contact RFC; all test data is rolled back."""
from unittest.mock import patch
import test_stock_return_credit_orm as runner


def checks(env):
    from odoo.exceptions import ValidationError
    from odoo.tests import Form
    from odoo.tools.convert import convert_file

    env = env(context=dict(env.context, no_vat_validation=True))
    env.registry.init_models(env.cr, ['crm.material.request.partner.wizzard'],
                             {'module': 'entintados_pdv'}, install=False)
    convert_file(env, 'entintados_pdv', 'wizards/crm_material_request_partner_wizzard_views.xml', {}, mode='update')
    Partner = env['res.partner']
    def contact(vat=None, **extra):
        return Partner.create(dict({'name': 'RFC demo check', 'phone': '5551234567', 'vat': vat}, **extra))
    def rejected(action):
        try:
            with env.cr.savepoint():
                action()
        except ValidationError:
            return
        raise AssertionError('Expected duplicate/format validation')

    specific = contact('  zqaz900101a11  ')
    assert specific.vat == 'ZQAZ900101A11'
    rejected(lambda: contact('zqaz900101a11'))
    specific.active = False
    rejected(lambda: contact('ZQAZ900101A11'))
    specific.active = True
    first, second = contact(), contact()
    rejected(lambda: (first | second).write({'vat': 'ZQAZ900102A11'}))
    rejected(lambda: Partner.create([
        {'name': name, 'phone': '5551234567', 'vat': vat}
        for name, vat in [('Batch 1', 'ZQAZ900103A11'), ('Batch 2', 'zqaz900103a11')]]))
    for generic in ('XAXX010101000', 'XEXX010101000'):
        records = contact(generic.lower()) | contact(generic)
        records.write({'vat': generic})
        assert all(p.vat == generic for p in records)

    def wizard(vat=None):
        lead = env['crm.lead'].create({'name': 'Demo RFC wizard'})
        with Form(env['crm.material.request.partner.wizzard'].with_context(default_lead_id=lead.id),
                  view='entintados_pdv.view_crm_material_request_partner_wizard_form') as form:
            form.partner_name = 'Demo optional RFC'
            form.partner_phone = '5551234567'
            if vat is not None:
                form.partner_vat = vat
        return form.record, lead

    for vat in (None, '', '   ', ' xaxx010101000 ', 'XEXX010101000'):
        record, lead = wizard(vat)
        assert record.uses_generic_rfc
        with patch.object(type(lead), 'action_request_material_output', return_value={'type': 'ir.actions.act_window_close'}) as resume:
            record.action_confirm()
            assert resume.call_count == 1
        assert lead.partner_id.vat == ((vat or '').strip().upper() or 'XAXX010101000')
        assert lead.partner_id.is_customer and lead.partner_id.phone == '5551234567'

    record, lead = wizard(' zqaz900104a11 ')
    assert not record.uses_generic_rfc and record.partner_vat == 'ZQAZ900104A11'
    with patch.object(type(lead), 'action_request_material_output', return_value={}):
        record.action_confirm()
    assert lead.partner_id.vat == 'ZQAZ900104A11'
    duplicate, duplicate_lead = wizard('ZQAZ900104A11')
    with patch.object(type(lead), 'action_request_material_output', return_value={}) as resume:
        rejected(duplicate.action_confirm)
        assert not duplicate_lead.partner_id and not resume.called
    invalid, invalid_lead = wizard('INVALID')
    rejected(invalid.action_confirm)
    assert not invalid_lead.partner_id
    linked_lead = env['crm.lead'].create({'name': 'Link existing RFC'})
    link = env['crm.material.request.partner.wizzard'].create({
        'lead_id': linked_lead.id, 'action_type': 'existing', 'partner_id': specific.id})
    with patch.object(type(linked_lead), 'action_request_material_output', return_value={}):
        link.action_confirm()
    assert linked_lead.partner_id == specific and specific.vat == 'ZQAZ900101A11'
    return 'PASS: RFC único activo/archivado y por lotes, genéricos repetibles, RFC opcional/normalizado en demos, advertencia, valor predeterminado, duplicados/formatos inválidos y vinculación existente.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
