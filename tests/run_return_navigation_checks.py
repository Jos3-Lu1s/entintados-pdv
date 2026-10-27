"""Smart links, credit indicators and boolean filter domains; always rolled back."""
import test_stock_return_credit_orm as runner


def checks(env):
    from odoo import Command
    from odoo.tools.convert import convert_file

    journal = env['account.journal'].search([
        ('company_id', '=', env.company.id), ('type', '=', 'sale')], limit=1)
    account = env['account.account'].search([
        ('company_ids', 'in', env.company.ids), ('account_type', '=', 'income')], limit=1)
    partner = env['res.partner'].create({'name': 'Navigation customer', 'phone': '5551234567'})
    values = {'journal_id': journal.id, 'partner_id': partner.id,
              'invoice_line_ids': [Command.create({'product_id': env['account.move']._reajuste_product().id,
                                                    'account_id': account.id, 'quantity': 1, 'price_unit': 100})]}
    invoice = env['account.move'].create(dict(values, move_type='out_invoice'))
    other = env['account.move'].create(dict(values, move_type='out_invoice'))
    assert not invoice.has_return_credit
    credit = env['account.move'].create(dict(values, move_type='out_refund', reversed_entry_id=invoice.id))
    assert invoice.has_return_credit and invoice.return_credit_count == 1
    assert credit.action_return_invoices()['res_id'] == invoice.id
    for operator, value, expected in [('=', True, invoice), ('!=', False, invoice),
                                      ('in', [True], invoice), ('=', False, other),
                                      ('!=', True, other), ('not in', [True], other)]:
        found = env['account.move'].search([('id', 'in', (invoice | other).ids),
                                          ('has_return_credit', operator, value)])
        assert found == expected, (operator, value, found.ids)
    assert env['account.move'].search([('id', 'in', (invoice | other).ids),
                                      ('has_return_credit', 'in', [True, False])]) == invoice | other
    credit.button_cancel()
    assert not invoice.has_return_credit and invoice.return_credit_count == 0
    assert not env['account.move'].search([('id', '=', invoice.id), ('has_return_credit', '=', True)])
    convert_file(env, 'entintados_pdv', 'views/return_credit_navigation_views.xml', {}, mode='update')
    return 'PASS: botones, aviso actualizado al cancelar, filtros positivos/negativos y vistas contables.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
