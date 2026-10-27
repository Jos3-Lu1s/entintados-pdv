"""Devolución de ticket cerrado no facturado; todos los cambios se revierten."""
import logging
from unittest.mock import patch
import test_stock_return_credit_orm as runner


def checks(env):
    from odoo import Command, fields
    from odoo.exceptions import UserError, AccessError
    from odoo.tools.convert import convert_file
    from odoo.tests import Form
    logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
    env.registry.init_models(env.cr, ['pos.accounting.return', 'pos.accounting.return.line', 'pos.order',
        'pos.order.line', 'stock.picking', 'account.move', 'account.payment'], {'module': 'entintados_pdv'}, install=False)
    convert_file(env, 'entintados_pdv', 'security/ir.model.access.csv', {}, mode='update')
    convert_file(env, 'entintados_pdv', 'views/pos_accounting_return_views.xml', {}, mode='update')

    def account(code, kind, reconcile=False):
        return env['account.account'].create({'name': code, 'code': code, 'account_type': kind,
            'company_ids': [Command.set(env.company.ids)], 'reconcile': reconcile})
    income = account('PRINC', 'income')
    cash = account('PRCASH', 'asset_cash')
    receivable = account('PRREC', 'asset_receivable', True)
    general = env['account.journal'].create({'name': 'QA POS cierre', 'code': 'PRCL', 'type': 'general'})
    sales = env['account.journal'].create({'name': 'QA POS notas', 'code': 'PRCR', 'type': 'sale'})
    bank = env['account.journal'].create({'name': 'QA POS cobro', 'code': 'PRBK', 'type': 'bank', 'default_account_id': cash.id})
    method = env['pos.payment.method'].create({'name': 'QA cobro original', 'journal_id': bank.id})
    partner = env['res.partner'].create({'name': 'QA cliente ticket', 'phone': '5551234567',
        'property_account_receivable_id': receivable.id})
    product = env['product.product'].create({'name': 'QA producto ticket', 'default_code': 'QA-RETURN-POS',
        'is_storable': True, 'list_price': 100, 'standard_price': 40, 'property_account_income_id': income.id,
        'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()]})
    config = env['pos.config'].create({'name': 'QA devolución contable POS', 'journal_id': general.id,
        'invoice_journal_id': sales.id, 'payment_method_ids': [Command.set(method.ids)]})
    session = env['pos.session'].create({'config_id': config.id, 'user_id': env.uid})
    session.state = 'opened'
    order = env['pos.order'].create({'session_id': session.id, 'partner_id': partner.id,
        'state': 'paid', 'amount_total': 200, 'amount_tax': 0, 'amount_paid': 200, 'amount_return': 0,
        'lines': [Command.create({'product_id': product.id, 'qty': 2, 'price_unit': 100,
            'price_subtotal': 200, 'price_subtotal_incl': 200, 'tax_ids': [Command.clear()]})]})
    env['pos.payment'].create({'pos_order_id': order.id, 'payment_method_id': method.id, 'amount': 200})
    stock = config.picking_type_id.default_location_src_id
    env['stock.quant']._update_available_quantity(product, stock, 10)
    original = env['stock.picking']._create_picking_from_pos_order_lines(
        partner.property_stock_customer.id, order.lines, config.picking_type_id, partner)
    original.write({'pos_order_id': order.id, 'pos_session_id': session.id})
    assert original.state == 'done'
    closing = env['account.move'].create({'journal_id': general.id, 'line_ids': [
        Command.create({'account_id': cash.id, 'debit': 200}),
        Command.create({'account_id': income.id, 'credit': 200}),
    ]})
    closing.action_post()
    session.write({'state': 'closed', 'move_id': closing.id})
    order.state = 'done'
    assert order.accounting_return_eligible

    def rejected(action):
        try:
            with env.cr.savepoint():
                action()
        except (UserError, AccessError):
            return
        raise AssertionError('La validación debió rechazar la operación')

    with Form(env['pos.accounting.return']) as form:
        form.order_id = order
        form.reason = 'Devolución parcial de ticket'
        assert len(form.line_ids) == 1
        with form.line_ids.edit(0) as row:
            row.selected = True
            row.quantity = 1
    request = form.record
    assert request.amount_total == 100 and request.source_move_id == closing
    rejected(request.action_create_credit)
    request.action_prepare_return()
    assert request.state == 'returned' and request.stock_received
    assert order.lines.refunded_qty == 1
    picking = request.picking_ids
    assert picking and picking.move_ids.origin_returned_move_id in original.move_ids
    rejected(lambda: request.write({'reason': 'No editable'}))
    rejected(order._generate_pos_order_invoice)
    assert picking.state == 'done' and product.with_context(location=stock.id).qty_available == 9
    request.action_prepare_return()
    assert request.picking_ids == picking and picking.move_ids.quantity == 1
    request.action_create_credit()
    credit = request.credit_ids
    assert request.state == 'done' and credit.move_type == 'out_refund'
    assert credit.state == 'posted' and credit.amount_total == 100 and credit.amount_residual == 0
    assert credit.pos_return_source_move_id == closing and not order.account_move
    assert not credit.reversed_entry_id, 'No simular una factura original inexistente'
    assert credit.invoice_line_ids.account_id == income
    assert request.payment_ids.journal_id == bank and request.payment_ids.amount == 100
    assert request.payment_ids.move_id.state == 'posted'
    assert product.with_context(location=stock.id).qty_available == 9
    assert closing.state == 'posted' and sum(closing.line_ids.mapped('debit')) == 200
    request.action_create_credit()
    assert request.credit_ids == credit
    credit.button_cancel()
    assert request.state == 'returned' and request.payment_ids.state == 'canceled'
    assert picking.state == 'done' and order.lines.refunded_qty == 1
    request.action_create_credit()
    assert len(request.credit_ids) == 2 and len(request.picking_ids) == 1
    assert request.credit_ids.filtered(lambda c: c.state == 'posted').amount_residual == 0
    assert product.with_context(location=stock.id).qty_available == 9
    rejected(request.action_cancel)

    def draft_return(qty=1, user=None):
        return env['pos.accounting.return'].with_user(user or env.user).create({
            'order_id': order.id, 'partner_id': partner.id, 'journal_id': sales.id,
            'reason': 'QA devolución adicional', 'line_ids': [Command.create({
                'source_line_id': order.lines.id, 'selected': True, 'quantity': qty})]})

    rejected(draft_return(2).action_prepare_return)
    failed = draft_return()
    previous_stock = product.with_context(location=stock.id).qty_available
    with patch.object(type(env['stock.picking']), 'button_validate', return_value={'res_model': 'stock.backorder.confirmation'}):
        rejected(failed.action_prepare_return)
    assert failed.state == 'draft' and not failed.picking_ids and not failed.credit_ids
    assert product.with_context(location=stock.id).qty_available == previous_stock
    failed.action_cancel()
    pending = draft_return()
    class UndoReceipt(Exception):
        pass
    try:
        with env.cr.savepoint():
            pending.action_prepare_return()
            assert order.lines.refunded_qty == 2 and pending.picking_ids.state == 'done'
            rejected(draft_return().action_prepare_return)
            rejected(pending.action_cancel)
            raise UndoReceipt()
    except UndoReceipt:
        pass
    pending.action_cancel()
    assert not pending.picking_ids and order.lines.refunded_qty == 1

    def reject_changed_source(values):
        def attempt():
            order.write(values)
            draft_return().action_prepare_return()
        rejected(attempt)
    reject_changed_source({'state': 'draft'})
    reject_changed_source({'account_move': credit.id})
    reject_changed_source({'split_invoice_ids': [Command.set(credit.ids)]})
    reject_changed_source({'l10n_mx_edi_cfdi_state': 'global_sent'})
    def unpaid_attempt():
        order.payment_ids.amount = 10
        draft_return().action_prepare_return()
    rejected(unpaid_attempt)
    assert not order.account_move and order.payment_ids.amount == 200

    def user(login, group):
        return env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'company_id': env.company.id,
            'company_ids': [Command.set(env.company.ids)],
            'group_ids': [Command.set((env.ref('base.group_user') | env.ref(group)).ids)]})
    accountant = user('qa_pos_return_accountant', 'account.group_account_user')
    warehouse_user = user('qa_pos_return_warehouse', 'stock.group_stock_user')
    cashier = user('qa_pos_return_cashier', 'point_of_sale.group_pos_user')
    assert not accountant.has_group('point_of_sale.group_pos_user')
    env.flush_all()
    rejected(lambda: order.with_user(cashier).action_new_accounting_return())
    final_return = draft_return(user=accountant)
    final_return.action_prepare_return()
    warehouse_picking = final_return.sudo().picking_ids.with_user(warehouse_user)
    assert warehouse_picking.read(['pos_accounting_return_name'])[0]['pos_accounting_return_name'] == final_return.name
    assert warehouse_picking.state == 'done' and final_return.state == 'returned'
    final_return.action_create_credit()
    assert final_return.state == 'done' and final_return.credit_ids.amount_residual == 0
    assert order.lines.refunded_qty == 2 and product.with_context(location=stock.id).qty_available == 10
    assert not order.accounting_return_eligible, 'No mostrar tickets totalmente devueltos'

    # Entintado: devolver la base trae sus dosis y sus importes proporcionalmente.
    colorant = env['product.product'].create({'name': 'QA colorante devolución', 'default_code': 'QA-RETURN-TINT',
        'is_storable': True, 'tracking': 'lot', 'tint_role': 'colorant', 'uom_id': env.ref('entintados_pdv.uom_tint_point').id,
        'property_account_income_id': income.id,
        'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()]})
    colorant_lot = env['stock.lot'].create({'name': 'QA-RETURN-TINT-LOT', 'product_id': colorant.id})
    env['stock.quant']._update_available_quantity(colorant, stock, 100, lot_id=colorant_lot)
    tint_session = env['pos.session'].create({'config_id': config.id, 'user_id': env.uid})
    tint_session.state = 'opened'
    tint_order = env['pos.order'].create({'session_id': tint_session.id, 'partner_id': partner.id,
        'state': 'paid', 'amount_total': 230, 'amount_tax': 0, 'amount_paid': 230, 'amount_return': 0,
        'lines': [Command.create({'product_id': product.id, 'qty': 2, 'price_unit': 100,
            'price_subtotal': 200, 'price_subtotal_incl': 200, 'tax_ids': [Command.clear()]}),
            Command.create({'product_id': colorant.id, 'qty': 6, 'price_unit': 5,
            'price_subtotal': 30, 'price_subtotal_incl': 30, 'tax_ids': [Command.clear()]})]})
    root = tint_order.lines.filtered(lambda l: l.product_id == product)
    child = tint_order.lines.filtered(lambda l: l.product_id == colorant)
    child.pack_lot_ids = [Command.create({'lot_name': colorant_lot.name})]
    child.combo_parent_id = root
    env['pos.payment'].create({'pos_order_id': tint_order.id, 'payment_method_id': method.id, 'amount': 230})
    tint_original = env['stock.picking']._create_picking_from_pos_order_lines(
        partner.property_stock_customer.id, tint_order.lines, config.picking_type_id, partner)
    tint_original.write({'pos_order_id': tint_order.id, 'pos_session_id': tint_session.id})
    tint_closing = env['account.move'].create({'journal_id': general.id, 'line_ids': [
        Command.create({'account_id': cash.id, 'debit': 230}), Command.create({'account_id': income.id, 'credit': 230})]})
    tint_closing.action_post()
    tint_session.write({'state': 'closed', 'move_id': tint_closing.id})
    tint_order.state = 'done'
    tint_return = env['pos.accounting.return'].create({'order_id': tint_order.id, 'partner_id': partner.id,
        'journal_id': sales.id, 'reason': 'Entintado parcial', 'line_ids': [Command.create({
            'source_line_id': root.id, 'selected': True, 'quantity': 1})]})
    assert tint_return.amount_total == 115
    tint_return.action_prepare_return()
    assert root.refunded_qty == 1 and child.refunded_qty == 3
    assert len(tint_return.picking_ids) == 1 and tint_return.picking_ids.state == 'done'
    assert tint_return.picking_ids.move_line_ids.filtered(lambda l: l.product_id == colorant).lot_id == colorant_lot
    assert tint_return.state == 'returned'
    tint_return.action_create_credit()
    assert tint_return.credit_ids.amount_total == 115 and tint_return.credit_ids.amount_residual == 0
    assert tint_return.credit_ids.invoice_line_ids.filtered(lambda l: l.pos_return_source_line_id == child).return_source_quantity == 3
    assert tint_return.credit_ids.invoice_line_ids.product_id == env['account.move']._reajuste_product()
    assert tint_return.credit_ids.return_picking_count == 1
    assert tint_return.credit_ids.action_return_ticket()['res_id'] == tint_order.id
    assert tint_return.credit_ids.return_payment_count == 1
    assert colorant.with_context(location=stock.id).qty_available == 97
    # Existing pending returns/backorders also finish without a second manual receipt.
    legacy = env['pos.accounting.return'].create({'order_id': tint_order.id, 'partner_id': partner.id,
        'journal_id': sales.id, 'reason': 'Recepción anterior pendiente', 'line_ids': [Command.create({
            'source_line_id': root.id, 'selected': True, 'quantity': 1})]})
    with patch.object(type(legacy), '_auto_validate_return_pickings', return_value=None):
        legacy.action_prepare_return()
    first_receipt = legacy.picking_ids.with_user(warehouse_user)
    for move in first_receipt.move_ids:
        move.quantity = 1
        move.picked = True
    first_receipt.with_context(skip_backorder=True, skip_sms=True).button_validate()
    assert len(legacy.picking_ids) == 2 and legacy.state == 'waiting_stock'
    legacy.action_prepare_return()
    assert legacy.state == 'returned' and all(p.state == 'done' for p in legacy.picking_ids)
    assert colorant.with_context(location=stock.id).qty_available == 100
    # Impuestos y descuento: corregir solo las cuentas del ticket dentro del cierre.
    vat = account('PRVAT', 'liability_current')
    tax = env['account.tax'].create({'name': 'QA IVA devolución 16', 'amount': 16, 'type_tax_use': 'sale'})
    (tax.invoice_repartition_line_ids | tax.refund_repartition_line_ids).filtered(lambda l: l.repartition_type == 'tax').account_id = vat
    tax_session = env['pos.session'].create({'config_id': config.id, 'user_id': env.uid})
    tax_session.state = 'opened'
    tax_order = env['pos.order'].create({'session_id': tax_session.id, 'partner_id': partner.id,
        'state': 'paid', 'amount_total': 104.4, 'amount_tax': 14.4, 'amount_paid': 104.4, 'amount_return': 0,
        'lines': [Command.create({'product_id': product.id, 'qty': 1, 'price_unit': 100, 'discount': 10,
            'price_subtotal': 90, 'price_subtotal_incl': 104.4, 'tax_ids': [Command.set(tax.ids)]})]})
    env['pos.payment'].create({'pos_order_id': tax_order.id, 'payment_method_id': method.id, 'amount': 104.4})
    tax_original = env['stock.picking']._create_picking_from_pos_order_lines(
        partner.property_stock_customer.id, tax_order.lines, config.picking_type_id, partner)
    tax_original.write({'pos_order_id': tax_order.id, 'pos_session_id': tax_session.id})
    tax_closing = env['account.move'].create({'journal_id': general.id, 'line_ids': [
        Command.create({'account_id': cash.id, 'debit': 104.4}),
        Command.create({'account_id': income.id, 'credit': 90, 'tax_ids': [Command.set(tax.ids)]}),
        Command.create({'account_id': vat.id, 'credit': 14.4,
            'tax_repartition_line_id': tax.invoice_repartition_line_ids.filtered(lambda l: l.repartition_type == 'tax').id}),
    ]})
    tax_closing.action_post()
    tax_session.write({'state': 'closed', 'move_id': tax_closing.id})
    tax_order.state = 'done'
    tax_return = env['pos.accounting.return'].create({'order_id': tax_order.id, 'partner_id': partner.id,
        'journal_id': sales.id, 'reason': 'Devolución con descuento e IVA', 'line_ids': [Command.create({
            'source_line_id': tax_order.lines.id, 'selected': True, 'quantity': 1})]})
    tax_return.action_prepare_return()
    assert tax_return.picking_ids.state == 'done' and tax_return.stock_received
    tax_return.action_create_credit()
    assert tax_return.credit_ids.amount_total == 104.4 and tax_return.credit_ids.amount_residual == 0
    assert tax_return.credit_ids.amount_tax == 14.4
    assert tax_return.credit_ids.invoice_line_ids.account_id == income
    assert tax_return.credit_ids.line_ids.filtered('tax_line_id').account_id == vat
    # Partial serial returns identify the sold serial, without validating a receipt manually.
    serial_product = env['product.product'].create({'name': 'QA producto serial', 'is_storable': True,
        'tracking': 'serial', 'property_account_income_id': income.id, 'taxes_id': [Command.clear()]})
    serials = env['stock.lot'].create([{'name': 'QA-RETURN-SERIAL-%s' % i, 'product_id': serial_product.id} for i in (1, 2)])
    for lot in serials:
        env['stock.quant']._update_available_quantity(serial_product, stock, 1, lot_id=lot)
    serial_session = env['pos.session'].create({'config_id': config.id, 'user_id': env.uid})
    serial_session.state = 'opened'
    serial_order = env['pos.order'].create({'session_id': serial_session.id, 'partner_id': partner.id,
        'state': 'paid', 'amount_total': 20, 'amount_tax': 0, 'amount_paid': 20, 'amount_return': 0,
        'lines': [Command.create({'product_id': serial_product.id, 'qty': 2, 'price_unit': 10,
            'price_subtotal': 20, 'price_subtotal_incl': 20, 'tax_ids': [Command.clear()],
            'pack_lot_ids': [Command.create({'lot_name': lot.name}) for lot in serials]})]})
    env['pos.payment'].create({'pos_order_id': serial_order.id, 'payment_method_id': method.id, 'amount': 20})
    serial_delivery = env['stock.picking']._create_picking_from_pos_order_lines(
        partner.property_stock_customer.id, serial_order.lines, config.picking_type_id, partner)
    serial_delivery.write({'pos_order_id': serial_order.id, 'pos_session_id': serial_session.id})
    serial_closing = env['account.move'].create({'journal_id': general.id, 'line_ids': [
        Command.create({'account_id': cash.id, 'debit': 20}), Command.create({'account_id': income.id, 'credit': 20})]})
    serial_closing.action_post()
    serial_session.write({'state': 'closed', 'move_id': serial_closing.id})
    serial_order.state = 'done'
    serial_return = env['pos.accounting.return'].create({'order_id': serial_order.id, 'partner_id': partner.id,
        'journal_id': sales.id, 'reason': 'Serie parcial', 'line_ids': [Command.create({
            'source_line_id': serial_order.lines.id, 'selected': True, 'quantity': 1})]})
    rejected(serial_return.action_prepare_return)
    assert serial_return.state == 'draft' and not serial_return.picking_ids
    serial_return.return_lot_names = 'UNSOLD-SERIAL'
    rejected(serial_return.action_prepare_return)
    serial_return.return_lot_names = serials[1].name
    serial_return.action_prepare_return()
    assert serial_return.picking_ids.state == 'done'
    assert serial_return.picking_ids.move_line_ids.lot_id == serials[1]
    assert not serial_return.credit_ids
    rejected(lambda: serial_return.write({'return_lot_names': serials[0].name}))
    serial_return.action_create_credit()
    assert serial_return.credit_ids.amount_total == 10 and not serial_return.credit_ids.amount_residual
    remaining_serial = env['pos.accounting.return'].create({'order_id': serial_order.id, 'partner_id': partner.id,
        'journal_id': sales.id, 'reason': 'Serie restante', 'line_ids': [Command.create({
            'source_line_id': serial_order.lines.id, 'selected': True, 'quantity': 1})]})
    remaining_serial.action_prepare_return()
    assert remaining_serial.picking_ids.move_line_ids.lot_id == serials[0]
    assert serial_product.with_context(location=stock.id).qty_available == 2
    return 'PASS: recepciones automáticas, parciales, lotes/series originales, rollback ante errores, pendientes anteriores, permisos contabilidad/almacén/caja, nota/pago conciliado, reintentos y entintados proporcionales.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
