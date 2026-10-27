"""Customer adjustment checks against a local database; always rolled back."""
import test_stock_return_credit_orm as runner


def checks(env):
    from odoo import Command, fields
    from odoo.exceptions import UserError
    from odoo.tests import Form
    from odoo.tools.convert import convert_file

    env.registry.init_models(env.cr, ['customer.account.adjustment', 'customer.account.adjustment.line', 'account.move', 'account.payment'],
                             {'module': 'entintados_pdv'}, install=False)
    for filename in ('security/ir.model.access.csv', 'views/customer_account_adjustment_views.xml'):
        # Demo roles are a separate pending feature; load their definitions so
        # the shared access CSV can also be validated in this transaction.
        if filename.endswith('.csv'):
            env.registry.init_models(env.cr, ['crm.lead'], {'module': 'entintados_pdv'}, install=False)
            convert_file(env, 'entintados_pdv', 'security/demo_flow_security.xml', {}, mode='update')
        convert_file(env, 'entintados_pdv', filename, {}, mode='update')

    def account(kind, code, reconcile=False):
        return env['account.account'].create({
            'name': 'Customer adjustment ' + kind, 'code': code, 'account_type': kind,
            'company_ids': [Command.set(env.company.ids)], 'reconcile': reconcile,
        })

    income = account('income', 'AJCINC')
    receivable = account('asset_receivable', 'AJCREC', True)
    cash = account('asset_current', 'AJCCSH')
    journal = env['account.journal'].create({'name': 'Customer adjustment sales', 'code': 'AJCS', 'type': 'sale'})
    settlement = env['account.journal'].create({'name': 'Customer adjustment payments', 'code': 'AJCP',
                                               'type': 'bank', 'default_account_id': cash.id})
    settlement.outbound_payment_method_line_ids.filtered(lambda line: line.code == 'manual').payment_account_id = cash
    partner = env['res.partner'].create({'name': 'Adjustment customer', 'phone': '5551234567',
                                        'property_account_receivable_id': receivable.id})
    other = env['res.partner'].create({'name': 'Other adjustment customer', 'phone': '5551234568',
                                      'property_account_receivable_id': receivable.id})
    tax = env['account.tax'].create({'name': 'Adjustment test 16%', 'amount': 16,
                                    'type_tax_use': 'sale', 'amount_type': 'percent'})
    product = env['product.product'].create({'name': 'Customer adjustment product'})

    def invoice(customer=partner, paid=True, post=True, price_unit=125, collections=None):
        move = env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': customer.id, 'journal_id': journal.id,
            'invoice_date': fields.Date.context_today(customer),
            'invoice_line_ids': [Command.create({'name': 'Adjustment sale', 'account_id': income.id,
                                                 'product_id': product.id, 'quantity': 1, 'price_unit': price_unit,
                                                 'discount': 20, 'tax_ids': [Command.set(tax.ids)]})],
        })
        if post:
            # El número se asigna al publicar; la auditoría debe admitir False
            # tanto con referencia como sin ella (también sucede en las notas).
            move.name = False
            assert not move.name and not move.ref
            move.action_post()
            amount = move.amount_total if paid else 10
            for bank, received in collections or [(settlement, amount)]:
                payment = env['account.move'].create({'journal_id': bank.id, 'line_ids': [
                    Command.create({'account_id': cash.id, 'debit': received}),
                    Command.create({'account_id': receivable.id, 'partner_id': customer.id, 'credit': received}),
                ]})
                payment.action_post()
                (move | payment).line_ids.filtered(lambda line: line.account_id == receivable and not line.reconciled).reconcile()
        return move

    first, second = invoice(), invoice()
    excluded = invoice(paid=False) | invoice(post=False) | invoice(other)
    with Form(env['customer.account.adjustment']) as form:
        form.partner_id = partner
        form.journal_id = journal
        form.reason = 'Customer partial credit'
        assert len(form.line_ids) == 2
        for index in range(len(form.line_ids)):
            with form.line_ids.edit(index) as line:
                if line.invoice_id == first:
                    line.selected = True
                    line.amount = 23.45
    adjustment = form.record
    assert set(adjustment.line_ids.invoice_id.ids) == set((first | second).ids)
    assert not (adjustment.line_ids.invoice_id & excluded)
    assert adjustment.invoice_count == 1 and adjustment.amount_total == 23.45
    adjustment.line_ids.filtered(lambda line: not line.selected).write({'amount': 9999})
    adjustment.action_create_credits()
    credit = adjustment.credit_ids
    assert len(credit) == 1 and credit.reversed_entry_id == first
    assert credit.invoice_line_ids.product_id == env['account.move']._reajuste_product()
    assert credit.action_return_invoices()['res_id'] == first.id
    assert credit.return_payment_count == 1
    assert credit.state == 'posted' and credit.amount_total == 23.45 and credit.amount_residual == 0
    payments = credit._get_reconciled_payments()
    assert len(payments) == 1 and payments.journal_id == settlement and payments.amount == 23.45
    assert payments.payment_type == 'outbound'
    assert first.payment_state == 'paid' and first.amount_residual == 0
    source = first.invoice_line_ids.filtered(lambda line: line.product_id == product)
    assert abs(first._return_line_capacities()[source.id] - 0.7978) < 0.00001
    adjustment.action_create_credits()
    assert adjustment.credit_ids == credit
    assert credit._get_reconciled_payments() == payments

    def rejected(action):
        try:
            with env.cr.savepoint():
                action()
        except UserError:
            return
        raise AssertionError('Expected validation rejection')

    rejected(lambda: adjustment.write({'partner_id': other.id}))
    rejected(lambda: adjustment.line_ids.write({'amount': 99}))
    rejected(adjustment.unlink)

    def group(source=first, amount=1, selected=True):
        return env['customer.account.adjustment'].create({
            'partner_id': partner.id, 'reason': 'More customer adjustment', 'journal_id': journal.id,
            'line_ids': [Command.create({'invoice_id': source.id, 'amount': amount, 'selected': selected})],
        })

    rejected(group(amount=0).action_create_credits)
    rejected(group(amount=-1).action_create_credits)
    rejected(group(amount=116).action_create_credits)
    rejected(group(selected=False).action_create_credits)
    rejected(group(source=excluded[:1]).action_create_credits)
    remaining = group(amount=92.55)
    remaining.action_create_credits()
    assert remaining.credit_ids.amount_total == 92.55
    assert first.payment_state == 'paid'
    assert abs(first._return_line_capacities()[source.id]) < 0.00001
    rejected(group(amount=0.01).action_create_credits)
    second_group = group(source=second, amount=50)
    draft_credit = second._reverse_moves(default_values_list=[{'auto_post': 'no'}], cancel=False)
    rejected(second_group.action_create_credits)
    draft_credit.button_cancel()
    second_group.action_create_credits()
    assert second_group.credit_ids.amount_residual == 0 and second.payment_state == 'paid'

    # Divided invoices are found from either the main invoice or an extra one.
    split_first, split_second = invoice(), invoice(price_unit=375)
    session = env['pos.session'].search([('company_id', '=', env.company.id)], limit=1)
    assert session, 'A local POS session is needed to test split invoice relationships'

    def link_split(invoices, pos_session=None):
        return env['pos.order'].create({
            'session_id': (pos_session or session).id, 'partner_id': partner.id,
            'amount_total': sum(invoices.mapped('amount_total')), 'amount_tax': 0,
            'amount_paid': sum(invoices.mapped('amount_total')), 'amount_return': 0,
            'account_move': invoices[0].id, 'split_invoice_ids': [Command.set(invoices.ids)],
            'invoice_split_count': len(invoices),
        })

    link_split(split_first | split_second)
    loaded = group(source=split_first)
    loaded._onchange_scope()
    split_rows = loaded.line_ids.filtered(lambda line: line.invoice_id in split_first | split_second)
    assert len(split_rows) == 1
    assert set(split_rows.invoice_ids.ids) == set((split_first | split_second).ids)
    assert split_rows.invoice_total == 464
    split_adjustment = group(source=split_second, amount=100)
    split_adjustment.action_create_credits()
    for note in split_adjustment.credit_ids:
        assert note.return_invoice_count == 2
        assert set(note.return_related_invoice_ids.ids) == set((split_first | split_second).ids)
        assert note.action_return_invoices()['domain'] == [('id', 'in', note.return_related_invoice_ids.ids)]
    assert split_first.return_invoice_count == 2 and split_first.has_return_credit
    assert split_adjustment.credit_count == 2 and split_adjustment.invoice_count == 2
    assert split_adjustment.credit_ids.filtered(lambda credit: credit.reversed_entry_id == split_first).amount_total == 25
    assert split_adjustment.credit_ids.filtered(lambda credit: credit.reversed_entry_id == split_second).amount_total == 75
    assert sum(split_adjustment.credit_ids.mapped('amount_residual')) == 0
    assert sum(split_adjustment.credit_ids._get_reconciled_payments().mapped('amount')) == 100
    assert all(move.payment_state == 'paid' for move in split_first | split_second)
    repeated = group(source=split_first, amount=40)
    repeated.line_ids = [Command.create({'invoice_id': split_second.id, 'amount': 60, 'selected': True})]
    repeated.action_create_credits()
    assert repeated.credit_count == 2 and sum(repeated.credit_ids.mapped('amount_total')) == 100
    tiny = group(source=split_first, amount=0.01)
    tiny.action_create_credits()
    assert tiny.credit_count == 1 and tiny.credit_ids.reversed_entry_id == split_second
    assert tiny.credit_ids.amount_total == 0.01
    reserved = split_first._reverse_moves(default_values_list=[{'auto_post': 'no'}], cancel=False)
    rejected(group(source=split_second, amount=10).action_create_credits)
    reserved.button_cancel()
    unpaid_first, unpaid_second = invoice(), invoice(paid=False)
    link_split(unpaid_first | unpaid_second)
    rejected(group(source=unpaid_first, amount=10).action_create_credits)
    loaded.action_reload_invoices()
    assert not loaded.line_ids.filtered(lambda line: line.invoice_id in unpaid_first | unpaid_second)
    extra_bank = env['account.journal'].create({'name': 'Second original bank', 'code': 'AJCB',
                                               'type': 'bank', 'default_account_id': cash.id})
    extra_bank.outbound_payment_method_line_ids.filtered(lambda line: line.code == 'manual').payment_account_id = cash
    mixed = invoice(collections=[(settlement, 29), (extra_bank, 87)])
    mixed_adjustment = group(source=mixed, amount=40)
    mixed_adjustment.action_create_credits()
    mixed_payments = mixed_adjustment.credit_ids._get_reconciled_payments()
    assert len(mixed_payments) == 2
    assert mixed_payments.filtered(lambda payment: payment.journal_id == settlement).amount == 10
    assert mixed_payments.filtered(lambda payment: payment.journal_id == extra_bank).amount == 30
    assert mixed_adjustment.credit_ids.amount_residual == 0 and mixed.amount_residual == 0
    mixed_adjustment.action_pay_credits()
    assert mixed_adjustment.credit_ids._get_reconciled_payments() == mixed_payments

    general = env['account.journal'].create({'name': 'POS collection clearing', 'code': 'AJCG', 'type': 'general'})
    pos_invoice = invoice(collections=[(general, 116)])
    rejected(group(source=pos_invoice, amount=20).action_create_credits)
    pos_method = env['pos.payment.method'].create({'name': 'Original POS bank', 'journal_id': settlement.id})
    test_config = env['pos.config'].create({'name': 'Adjustment payment test POS',
                                           'payment_method_ids': [Command.set(pos_method.ids)]})
    test_session = env['pos.session'].create({'config_id': test_config.id, 'user_id': env.uid})
    pos_order = link_split(pos_invoice, test_session)
    pos_order.account_move = False  # POS collects payment before invoicing.
    env['pos.payment'].create({'pos_order_id': pos_order.id, 'payment_method_id': pos_method.id, 'amount': 116})
    pos_order.account_move = pos_invoice
    pos_adjustment = group(source=pos_invoice, amount=20)
    pos_adjustment.action_create_credits()
    assert pos_adjustment.credit_ids.amount_residual == 0
    assert pos_adjustment.credit_ids._get_reconciled_payments().journal_id == settlement

    # Existing, already-generated notes can be settled without regenerating them.
    legacy_invoice = invoice()
    legacy_adjustment = group(source=legacy_invoice, amount=30)
    legacy_credit = legacy_invoice._reverse_moves(default_values_list=[{
        'customer_adjustment_id': legacy_adjustment.id, 'auto_post': 'no',
    }], cancel=False)
    legacy_credit.action_post()
    legacy_adjustment.action_pay_credits()
    assert legacy_credit.amount_residual == 0
    assert legacy_credit._get_reconciled_payments().amount == 116
    # Cancellation rolls back only this adjustment's credits and refund payments.
    old_credits = mixed_adjustment.credit_ids
    old_payments = mixed_adjustment.payment_ids
    assert len(old_payments) == 2
    rejected(lambda: mixed_adjustment.write({'state': 'cancel'}))
    mixed_adjustment.action_cancel()
    assert mixed_adjustment.state == 'cancel'
    assert all(credit.state == 'cancel' for credit in old_credits)
    assert all(payment.state == 'canceled' for payment in old_payments)
    assert mixed.payment_state == 'paid' and mixed.amount_residual == 0
    assert mixed_adjustment.credit_ids == old_credits and mixed_adjustment.payment_ids == old_payments
    mixed_adjustment.action_cancel()  # Cancellation is idempotent.
    rejected(mixed_adjustment.action_create_credits)
    rejected(mixed_adjustment.action_pay_credits)
    mixed_adjustment.action_draft()
    mixed_adjustment.line_ids.amount = 20
    mixed_adjustment.action_create_credits()
    new_credits = mixed_adjustment.credit_ids - old_credits
    assert mixed_adjustment.state == 'generated' and new_credits.amount_total == 20
    assert new_credits.amount_residual == 0
    assert all(credit.state == 'cancel' for credit in old_credits)
    assert all(payment.state == 'canceled' for payment in old_payments)
    assert mixed.payment_state == 'paid'
    rejected(mixed_adjustment.action_draft)
    split_credits, split_payments = split_adjustment.credit_ids, split_adjustment.payment_ids
    split_adjustment.action_cancel()
    assert all(credit.state == 'cancel' for credit in split_credits)
    assert all(payment.state == 'canceled' for payment in split_payments)
    assert all(move.payment_state == 'paid' for move in split_first | split_second)
    split_adjustment.action_draft()
    split_adjustment.action_create_credits()
    regenerated = split_adjustment.credit_ids - split_credits
    assert regenerated and all(note.amount_residual == 0 for note in regenerated)
    for note in regenerated:
        refund_payments = split_adjustment.payment_ids.filtered(lambda p: p.customer_adjustment_credit_id == note)
        assert refund_payments and all(p.state != 'canceled' for p in refund_payments)
        note.button_cancel()  # Cancel directly on each note, outside the adjustment.
        assert all(p.state == 'canceled' and p.move_id.state == 'cancel' for p in refund_payments)
    assert all(move.payment_state == 'paid' and move.amount_residual == 0 for move in split_first | split_second)
    split_adjustment.action_cancel()
    split_adjustment.action_draft()
    split_adjustment.action_create_credits()
    assert all(note.amount_residual == 0 for note in split_adjustment.credit_ids.filtered(lambda n: n.state == 'posted'))
    # Methods without an outstanding account still need a posted refund entry.
    settlement.outbound_payment_method_line_ids.filtered(lambda m: m.code == 'manual').payment_account_id = False
    fallback_adjustment = group(source=invoice(), amount=15)
    fallback_adjustment.action_create_credits()
    assert fallback_adjustment.payment_ids.move_id.state == 'posted'
    assert fallback_adjustment.payment_ids.outstanding_account_id == settlement.default_account_id
    fallback_adjustment.action_cancel()
    assert fallback_adjustment.payment_ids.state == 'canceled'
    draft_adjustment = group(source=second, amount=1)
    draft_adjustment.action_cancel()
    assert draft_adjustment.state == 'cancel' and not draft_adjustment.credit_ids
    draft_adjustment.action_draft()
    draft_adjustment.line_ids.amount = 2
    return 'PASS: proportional split credits, original-journal payments, cancellation of notes/refund payments only, original collections preserved, cancelled history, reset/edit/regenerate, repeated actions and validation limits.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
