"""Comprueba bitácoras operativas sin conservar datos de prueba."""
import test_stock_return_credit_orm as runner


def checks(env):
    import logging
    logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
    from odoo import Command, fields
    from odoo.exceptions import UserError
    from odoo.addons.entintados_pdv.models.business_chatter import operation, OPERATION

    partner = env['res.partner'].create({'name': 'QA bitácora', 'phone': '5551234567', 'is_purchase_contact': True})
    product = env['product.product'].create({'name': 'QA bitácora producto', 'default_code': 'QA-LOG',
        'list_price': 100, 'is_storable': True, 'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()]})
    active = {'tracking_disable': False, 'mail_notrack': False, 'lang': env.lang}
    sale = env['sale.order'].with_context(**active).create({'partner_id': partner.id})
    sentinel = sale.message_post(body='Mensaje anterior que debe conservarse')
    sale._track_finalize()

    def audit_messages(parent):
        parent._track_finalize()
        return env['mail.message'].search([('model', '=', parent._name), ('res_id', '=', parent.id)])

    sale.write({'client_order_ref': '<script>prueba</script>'})
    messages = audit_messages(sale)
    tracked = messages.filtered(lambda m: 'client_order_ref' in m.tracking_value_ids.field_id.mapped('name'))
    assert len(tracked) == 1 and 'Origen:' in tracked.body and 'Usuario:' in tracked.body
    assert sentinel.exists() and str(sentinel.body) == '<p>Mensaje anterior que debe conservarse</p>'
    before = audit_messages(sale)
    sale.write({'client_order_ref': '<script>prueba</script>'})
    assert audit_messages(sale) == before, 'Una escritura sin cambios no debe crear notas'
    line = env['sale.order.line'].with_context(**active).create({
        'order_id': sale.id, 'product_id': product.id, 'product_uom_qty': 1, 'price_unit': 80})
    before = audit_messages(sale)
    line.write({'product_uom_qty': 3, 'price_unit': 90, 'discount': 5, 'name': '<script>sin HTML</script>'})
    changes = audit_messages(sale) - before
    assert any('1.0 → 3.0' in str(m.body) and '80.0 → 90.0' in str(m.body) for m in changes)
    assert all('<script>' not in str(m.body) for m in changes)
    assert all(m.author_id == env.user.partner_id for m in changes)
    before = audit_messages(sale)
    line.unlink()
    assert any('Registro eliminado' in str(m.body) for m in audit_messages(sale) - before)

    purchase = env['purchase.order'].with_context(**active).create({'partner_id': partner.id})
    purchase_line = env['purchase.order.line'].with_context(**active).create({
        'order_id': purchase.id, 'product_id': product.id, 'product_qty': 1, 'price_unit': 30})
    before = audit_messages(purchase)
    purchase_line.write({'price_unit': 35, 'product_qty': 2})
    assert any('30.0 → 35.0' in str(m.body) for m in audit_messages(purchase) - before)
    purchase.button_confirm()
    assert purchase.state == 'purchase'
    picking = purchase.picking_ids.with_context(**active)
    before = audit_messages(picking)
    picking.write({'origin': 'QA cambio de origen'})
    messages = audit_messages(picking) - before
    assert len(messages.filtered(lambda m: 'origin' in m.tracking_value_ids.field_id.mapped('name'))) == 1
    before = audit_messages(picking)
    picking.move_ids.quantity = 2
    picking.move_ids.picked = True
    picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
    assert picking.state == 'done'
    assert any('Validar transferencia' in str(m.body) for m in audit_messages(picking) - before)
    lot = env['stock.lot'].with_context(**active).create({'name': 'QA-LOTE', 'product_id': product.id})
    lot.write({'ref': 'QA referencia modificada'})
    assert any('Origen:' in str(m.body) for m in audit_messages(lot))
    before = product.product_tmpl_id.message_ids
    env['stock.quant']._update_available_quantity(product, env.ref('stock.stock_location_stock'), 3)
    assert product.product_tmpl_id.message_ids - before, 'Las existencias sin transferencia se registran en producto'
    quant = env['stock.quant'].with_context(inventory_mode=True).search([
        ('product_id', '=', product.id), ('location_id', '=', env.ref('stock.stock_location_stock').id)], limit=1)
    target = quant.quantity + 1
    quant.inventory_quantity = target
    before = product.product_tmpl_id.message_ids
    quant._apply_inventory(date=fields.Date.today())
    assert quant.quantity == target
    assert any('Aplicar ajuste de inventario' in str(m.body) for m in product.product_tmpl_id.message_ids - before)

    income = env['account.account'].create({'name': 'QA bitácora ingreso', 'code': 'QALOGINC',
        'account_type': 'income', 'company_ids': [Command.set(env.company.ids)]})
    journal = env['account.journal'].create({'name': 'QA bitácora ventas', 'code': 'QALG', 'type': 'sale'})
    invoice = env['account.move'].with_context(**active).create({
        'move_type': 'out_invoice', 'partner_id': partner.id, 'journal_id': journal.id,
        'invoice_date': fields.Date.today(),
        'invoice_line_ids': [Command.create({'product_id': product.id, 'account_id': income.id,
            'quantity': 1, 'price_unit': 100, 'tax_ids': [Command.clear()]})]})
    before = audit_messages(invoice)
    invoice.invoice_line_ids.write({'price_unit': 110, 'discount': 10})
    assert any('100.0 → 110.0' in str(m.body) for m in audit_messages(invoice) - before)
    invoice.action_post()
    assert invoice.state == 'posted'
    assert any('Contabilizar' in str(m.body) for m in audit_messages(invoice))
    before = audit_messages(invoice)
    try:
        with env.cr.savepoint():
            with operation(invoice, 'Operación que debe revertirse'):
                invoice.write({'ref': 'NO DEBE QUEDAR'})
                raise UserError('Rollback de prueba')
    except UserError:
        pass
    assert not OPERATION.get()
    assert invoice.ref != 'NO DEBE QUEDAR' and audit_messages(invoice) == before
    # Los procesos internos que suprimen el tracking nativo también quedan registrados.
    before = audit_messages(invoice)
    invoice.with_context(tracking_disable=True).write({'ref': 'Cambio automático de prueba'})
    assert any('Cambio automático de prueba' in str(m.body) for m in audit_messages(invoice) - before)
    admin = env.ref('base.user_admin')
    sale.with_user(admin).sudo().write({'client_order_ref': 'Cambio por usuario con sudo'})
    messages = audit_messages(sale)
    user_changes = messages.filtered(lambda m: 'Cambio por usuario con sudo' in str(m.tracking_value_ids.mapped('new_value_char')))
    assert len(user_changes) == 1 and user_changes.author_id == admin.partner_id
    # El mensaje nativo de cantidad confirmada se conserva con su procedencia.
    confirmed_line = env['sale.order.line'].with_context(**active).create({
        'order_id': sale.id, 'product_id': product.id, 'product_uom_qty': 1, 'price_unit': 80})
    sale.action_confirm()
    before = audit_messages(sale)
    confirmed_line.product_uom_qty = 2
    changes = audit_messages(sale) - before
    quantity_notes = changes.filtered(lambda m: 'ordered quantity' in str(m.body).lower())
    assert len(quantity_notes) == 1 and 'Origen:' in quantity_notes.body
    assert not changes.filtered(lambda m: 'Cambios en' in str(m.body) and '1.0 → 2.0' in str(m.body))
    # La evidencia conserva el registro existente, sin duplicar el cambio.
    before = audit_messages(picking)
    picking.document_file = b'cHJ1ZWJh'
    changes = audit_messages(picking) - before
    assert len(changes) == 1 and 'cHJ1ZWJh' not in str(changes.body)
    # No alterar la bitácora previa de CRM ni la exclusión explícita de su tracking.
    lead = env['crm.lead'].with_context(tracking_disable=True).create({'name': 'QA CRM previo'})
    material = env['crm.material.line'].with_context(**active).create({
        'lead_id': lead.id, 'product_id': product.id, 'quantity': 1, 'uom_id': product.uom_id.id})
    before = lead.message_ids
    material.write({'quantity': 3})
    assert len(lead.message_ids - before) == 1
    before = lead.message_ids
    material.with_context(tracking_disable=True).write({'quantity': 4})
    assert lead.message_ids == before
    return 'PASS: ventas/compras y líneas, facturas/asientos, transferencias/lotes/existencias, origen, autor, mensajes anteriores, no-op, HTML escapado y rollback.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
