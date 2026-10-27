"""Contact seller ownership and warehouse stock checks; transaction rolled back."""
import logging
import test_stock_return_credit_orm as runner


def checks(env):
    from odoo import Command
    from odoo.exceptions import UserError
    from odoo.tools.convert import convert_file

    logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
    env.registry.init_models(env.cr, ['pos.order'], {'module': 'entintados_pdv'}, install=False)
    def user(suffix):
        return env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Owner check ' + suffix, 'login': 'owner-stock-' + suffix,
            'group_ids': [Command.set([env.ref('base.group_user').id])],
            'company_id': env.company.id, 'company_ids': [Command.set(env.company.ids)]})
    owner, cashier = user('seller'), user('cashier')
    partner = env['res.partner'].create({'name': 'Seller customer', 'phone': '5551234567', 'user_id': owner.id})
    other = env['res.partner'].create({'name': 'Other seller customer', 'phone': '5551234567', 'user_id': cashier.id})
    def rejected(action):
        try:
            with env.cr.savepoint():
                action()
        except UserError:
            return
        raise AssertionError('Expected seller/stock validation')
    assert partner.user_id == owner, ('initial', partner.user_id.ids, owner.ids)
    rejected(lambda: partner.write({'user_id': cashier.id}))
    assert partner.user_id == owner, ('after rejection', partner.user_id.ids, owner.ids)
    partner.write({'user_id': owner.id})
    sale = env['sale.order'].create({'partner_id': partner.id, 'user_id': cashier.id})
    assert sale.user_id == owner
    rejected(lambda: sale.write({'user_id': cashier.id}))
    sale.partner_id = other
    assert sale.user_id == cashier
    sale.partner_id = partner
    lead = env['crm.lead'].create({'name': 'Contact seller opportunity', 'partner_id': partner.id, 'user_id': cashier.id})
    assert lead.user_id == owner
    rejected(lambda: lead.write({'user_id': cashier.id}))
    lead.partner_id = other
    assert lead.user_id == cashier
    config = env['pos.config'].create({'name': 'Owner and stock checks'})
    session = env['pos.session'].create({'config_id': config.id, 'user_id': cashier.id})
    pos = env['pos.order'].create({'session_id': session.id, 'partner_id': partner.id,
        'user_id': cashier.id, 'contact_salesperson_id': cashier.id,
        'amount_total': 0, 'amount_tax': 0, 'amount_paid': 0, 'amount_return': 0})
    assert pos.user_id == cashier and pos.contact_salesperson_id == owner
    rejected(lambda: pos.write({'contact_salesperson_id': cashier.id}))
    assert pos._prepare_invoice_vals()['invoice_user_id'] == owner.id
    pos.partner_id = other
    assert pos.contact_salesperson_id == cashier
    assert partner.sale_owner_name == owner.name
    assert 'sale_owner_name' in env['res.partner']._load_pos_data_fields(config)
    for model, field in [('res.partner', 'user_id'), ('sale.order', 'user_id'),
                         ('account.move', 'invoice_user_id'), ('pos.order', 'contact_salesperson_id')]:
        assert env[model].fields_get([field])[field]['readonly']

    product = env['product.product'].create({'name': 'Stock owner checks', 'is_storable': True, 'type': 'consu'})
    service = env['product.product'].create({'name': 'Stock exempt service', 'type': 'service'})
    location = sale.warehouse_id.lot_stock_id
    def lines(quantities, item=product, uom=False):
        sale.order_line = [Command.clear()] + [Command.create({'product_id': item.id,
            'product_uom_qty': qty, 'product_uom_id': (uom or item.uom_id).id}) for qty in quantities]
        return sale
    def pos_stock(qty, item=product):
        return env['pos.order'].new({'config_id': config.id, 'company_id': config.company_id.id,
            'lines': [Command.create({'product_id': item.id, 'qty': qty})]})._check_pos_stock()
    rejected(lambda: lines([1]).action_confirm())
    rejected(lambda: pos_stock(1))
    # Fractional quantities must not round zero inventory into an allowed sale.
    rejected(lambda: pos_stock(0.01))
    env['stock.quant']._update_available_quantity(product, location, -1)
    rejected(lambda: lines([1])._check_stock_availability())
    env['stock.quant']._update_available_quantity(product, location, 4)
    lines([3])._check_stock_availability()
    rejected(lambda: lines([2, 2])._check_stock_availability())
    pack = env['uom.uom'].create({'name': 'Pair stock checks', 'relative_uom_id': product.uom_id.id, 'relative_factor': 2})
    rejected(lambda: lines([2], uom=pack)._check_stock_availability())
    lines([1], uom=pack)._check_stock_availability()
    quant = env['stock.quant'].search([('product_id', '=', product.id), ('location_id', '=', location.id)])
    quant.reserved_quantity = 2
    product.invalidate_recordset()
    rejected(lambda: lines([2])._check_stock_availability())
    lines([1])._check_stock_availability()
    lines([100], item=service)._check_stock_availability()
    pos_stock(100, service)
    income = env['account.account'].search([('account_type', '=', 'income'), ('company_ids', 'in', env.company.ids)], limit=1)
    invoice = env['account.move'].create({'move_type': 'out_invoice', 'partner_id': partner.id,
        'invoice_user_id': cashier.id, 'invoice_line_ids': [Command.create({'product_id': service.id,
            'account_id': income.id, 'price_unit': 10, 'quantity': 1, 'sale_line_ids': [Command.set(sale.order_line.ids)]})]})
    assert invoice.invoice_user_id == owner
    rejected(lambda: invoice.write({'invoice_user_id': cashier.id}))
    credit = invoice._reverse_moves(default_values_list=[{'auto_post': 'no'}])
    assert credit.invoice_user_id == owner
    # A different billing contact must not replace the POS selling contact's owner.
    pos.partner_id = partner
    billing_invoice = env['account.move'].create({'move_type': 'out_invoice', 'partner_id': other.id,
        'pos_order_ids': [Command.set(pos.ids)]})
    assert billing_invoice.invoice_user_id == owner
    split_invoice = env['account.move'].create({'move_type': 'out_invoice', 'partner_id': other.id})
    pos.split_invoice_ids = billing_invoice | split_invoice
    split_invoice.invoice_user_id = owner
    assert split_invoice._sale_contact_owner() == owner
    service.property_account_income_id = income
    split_pos = env['pos.order'].create({'session_id': session.id, 'partner_id': partner.id,
        'user_id': cashier.id, 'invoice_split_count': 2,
        'amount_total': 20, 'amount_tax': 0, 'amount_paid': 20, 'amount_return': 0,
        'lines': [Command.create({'product_id': service.id, 'qty': 2, 'price_unit': 10,
            'price_subtotal': 20, 'price_subtotal_incl': 20, 'tax_ids': [Command.clear()]})]})
    split_vals = split_pos._prepare_invoice_vals()
    split_vals['partner_id'] = other.id
    split_pos._create_invoice(split_vals)
    assert len(split_pos.split_invoice_ids) == 2
    assert split_pos.split_invoice_ids.invoice_user_id == owner
    convert_file(env, 'entintados_pdv', 'views/sale_contact_owner_views.xml', {}, mode='update')
    return 'PASS: vendedor fijo en contacto/CRM/venta/POS/factura/nota, cajero independiente, cambios de cliente, cero/negativo/fracciones, líneas acumuladas, UdM, reservas y servicios.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
