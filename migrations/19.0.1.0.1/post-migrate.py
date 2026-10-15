from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    moves = env['account.move'].search([
        ('state', '=', 'posted'),
        ('move_type', 'in', ['in_invoice', 'in_refund']),
        '|', ('vendor_credit_note_ids', '!=', False),
        ('vendor_credit_source_ids', '!=', False),
    ])
    moves._compute_payment_state()
