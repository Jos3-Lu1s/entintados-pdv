from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    reason = env.ref('entintados_pdv.vendor_credit_reason_return', raise_if_not_found=False)
    # Do not reinterpret existing financial documents as physical returns.
    if reason and not env['vendor.credit.group'].search_count([
            ('reason_id', '=', reason.id), ('state', '=', 'generated')]):
        reason.operation_type = 'return'
