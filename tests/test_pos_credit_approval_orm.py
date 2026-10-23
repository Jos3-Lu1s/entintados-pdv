"""Approval permissions and single-ticket authorization on Osel_Test; rollback."""
from unittest.mock import patch
from uuid import uuid4
from odoo import api, SUPERUSER_ID, Command
from odoo.exceptions import AccessError, UserError
from odoo.tools import config
from odoo.modules.registry import Registry


def rejects(callback, error=UserError):
    try:
        callback()
    except error:
        return
    raise AssertionError('Operation unexpectedly accepted')


def run():
    config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
    with Registry('Osel_Test').cursor() as cr:
        try:
            env = api.Environment(cr, SUPERUSER_ID, {
                'mail_notify_force_send': False, 'mail_auto_subscribe_no_notify': True,
            })
            pos = env['pos.config'].search([], limit=1)
            pos.action_prepare_deferred_payments()
            partner = env['res.partner'].create({
                'name': 'Credit approval regression', 'phone': '5551234567',
                'is_credit': True, 'is_customer': True, 'credit_limit': 100,
                'risk_invoice_open_include': True, 'risk_invoice_unpaid_include': True,
            })
            user = env['res.users'].with_context(no_reset_password=True).create({
                'name': 'Treasury approval regression', 'login': 'treasury-' + str(uuid4()),
                'company_id': pos.company_id.id, 'company_ids': [Command.set(pos.company_id.ids)],
                'group_ids': [Command.set(env.ref('base.group_user').ids)],
            })
            env['hr.employee'].create({
                'name': user.name, 'user_id': user.id, 'company_id': pos.company_id.id,
                'department_id': env.ref('entintados_pdv.hr_department_tesoreria').id,
            })
            session = pos.current_session_id or env['pos.session'].create({'config_id': pos.id})
            product = env['product.product'].create({'name': 'Approval test goods', 'type': 'service'})

            def request_for(uuid, total, credit, config=pos):
                saved = env['pos.order'].search([('uuid', '=', uuid)], limit=1)
                values = {
                    'partner_id': partner.id, 'amount_total': total, 'amount_tax': 0,
                    'amount_paid': 0, 'amount_return': 0,
                    'lines': [Command.clear(), Command.create({'product_id': product.id, 'qty': 1,
                              'price_unit': total, 'price_subtotal': total, 'price_subtotal_incl': total})],
                }
                if saved:
                    saved.write(values)
                else:
                    saved = env['pos.order'].create(dict(values, session_id=session.id, uuid=uuid))
                return config.request_credit_approval(partner.id, uuid, total, credit, 0, saved.id)

            uuid = str(uuid4())
            rejects(lambda: pos.request_credit_approval(partner.id, str(uuid4()), 200, 200))
            response = request_for(uuid, 200, 200)
            Approval = env['pos.credit.approval']
            request = Approval.browse(response['id'])
            assert request.state == 'pending'
            assert user in request.approver_user_ids
            native = request.approval_request_id
            assert native and native.category_id.pos_credit_category
            assert native.request_status == 'pending'
            assert user in native.category_id.approver_ids.user_id
            assert native.activity_ids.filtered(lambda activity: activity.user_id == user)
            assert request_for(uuid, 200, 200)['id'] == request.id
            assert request.order_id.uuid == uuid
            assert request.order_id.state == 'draft' and not request.order_id.account_move
            restored = pos.get_order_credit_approval(request.order_id.id)
            assert restored['id'] == request.id and restored['state'] == 'pending'
            outsider = env['res.users'].with_context(no_reset_password=True).create({
                'name': 'Unauthorized approval regression', 'login': 'outsider-' + str(uuid4()),
                'company_id': pos.company_id.id, 'company_ids': [Command.set(pos.company_id.ids)],
                'group_ids': [Command.set(env.ref('base.group_user').ids)],
            })
            # Even being listed as a responsible user cannot bypass department membership.
            request.write({'approver_user_ids': [Command.link(outsider.id)]})
            rejects(lambda: request.with_user(outsider).action_approve(), AccessError)
            rejects(lambda: native.with_user(outsider).action_approve(), AccessError)
            rejects(lambda: native.with_user(user).write({'amount': 1}), AccessError)
            rejects(lambda: request.with_user(user).write({'state': 'approved'}), AccessError)
            native.with_user(user).action_approve()
            assert request.state == 'approved' and request.decided_by == user
            assert native.request_status == 'approved'
            rejects(lambda: request.with_user(user).action_refuse(), AccessError)
            deferred = pos.payment_method_ids.filtered(lambda method: method.type == 'pay_later')[:1]
            order = request.order_id
            order.write({
                'to_invoice': True, 'pos_payment_flow': 'credit',
                'amount_total': 200, 'amount_tax': 0, 'amount_paid': 200, 'amount_return': 0,
                'payment_ids': [Command.create({'payment_method_id': deferred.id, 'amount': 200})],
            })
            rejects(lambda: order._require_credit_approval(partner, 199, 0))
            with patch.object(type(partner), '_pos_credit_exposure', lambda record, **kwargs: 300):
                rejects(lambda: order._require_credit_approval(partner, 200, 0))
            order._check_pos_deferred_payments(check_risk=True)
            assert request.state == 'used' and request.used_order_id == order
            order._check_pos_deferred_payments(check_risk=True)  # Idempotent pipeline retry.
            assert order._get_invoice_post_context().get('bypass_risk')
            rejects(lambda: native.with_user(user).action_cancel())
            assert request.state == 'used' and native.request_status == 'approved'
            cancel_uuid = str(uuid4())
            cancellable = Approval.browse(request_for(cancel_uuid, 250, 250)['id'])
            cancellable_native = cancellable.approval_request_id
            rejects(lambda: cancellable_native.with_user(outsider).action_cancel(), AccessError)
            cancellable_native.with_user(user).action_cancel()
            assert cancellable.state == 'void' and cancellable_native.request_status == 'cancel'
            assert not cancellable_native.activity_ids
            assert cancellable.decided_by == user
            replacement = Approval.browse(request_for(cancel_uuid, 250, 250)['id'])
            assert replacement != cancellable and replacement.state == 'pending'
            replacement.approval_request_id.with_user(user).action_approve()
            assert replacement.approval_request_id.with_user(user).can_cancel_pos_credit
            completed = Approval.browse(request_for(str(uuid4()), 250, 250)['id'])
            completed.approval_request_id.with_user(user).action_approve()
            completed.order_id.write({'state': 'paid'})
            assert not completed.approval_request_id.with_user(user).can_cancel_pos_credit
            rejects(lambda: completed.approval_request_id.with_user(user).action_cancel(), AccessError)
            outsider.write({'group_ids': [Command.link(env.ref('point_of_sale.group_pos_user').id)]})
            assert request_for(cancel_uuid, 250, 250, pos.with_user(outsider))['id'] == replacement.id
            cashier_request = Approval.browse(request_for(str(uuid4()), 250, 250, pos.with_user(outsider))['id'])
            cashier_request.approval_request_id.with_user(user).action_approve()
            # The requesting cashier can revoke an unused approval without editing approvers.
            cashier_request.approval_request_id.with_user(outsider).action_cancel()
            assert cashier_request.state == 'void' and cashier_request.cancelled_by == outsider
            assert cashier_request.decided_by == user
            other = request_for(str(uuid4()), 300, 300)
            refused = Approval.browse(other['id'])
            refused.with_user(user).action_refuse()
            assert refused.state == 'refused'
            assert refused.approval_request_id.request_status == 'refused'
            assert not refused.approval_request_id.with_user(user).can_cancel_pos_credit
            rejects(lambda: refused.approval_request_id.with_user(user).action_cancel(), AccessError)
            rejects(lambda: refused.approval_request_id.with_user(user).action_draft())
            # Existing decisions survive migration to the native application.
            legacy = request.copy({'order_uuid': str(uuid4()), 'state': 'approved', 'used_order_id': False})
            assert not legacy.approval_request_id
            migrated = legacy._create_native_request()
            assert migrated.request_status == 'approved' and legacy.state == 'approved'
            assert legacy._create_native_request() == migrated
            assert request_for(legacy.order_uuid, 200, 200)['id'] == legacy.id
            assert legacy.order_id and migrated.reference == legacy.order_id.pos_reference
            changed_uuid = str(uuid4())
            old = Approval.browse(request_for(changed_uuid, 400, 400)['id'])
            new = Approval.browse(request_for(changed_uuid, 450, 450)['id'])
            assert old.state == 'cancelled' and new != old
            assert old.approval_request_id.request_status == 'cancel'
            for approved in (False, True):
                cancel_test = Approval.browse(request_for(str(uuid4()), 500, 500)['id'])
                if approved:
                    cancel_test.with_user(user).action_approve()
                saved_order = cancel_test.order_id
                saved_order.action_pos_order_cancel()
                assert saved_order.state == 'cancel'
                assert cancel_test.state == 'void'
                assert cancel_test.approval_request_id.request_status == 'cancel'
                assert not cancel_test.approval_request_id.with_user(user).can_cancel_pos_credit
                assert cancel_test.cancel_reason == 'Orden del PdV cancelada.'
                assert cancel_test.cancelled_by == env.user and cancel_test.cancelled_on
                if approved:
                    assert cancel_test.decided_by == user  # Original approver stays in the audit trail.
                rejects(lambda: saved_order._require_credit_approval(partner, 500, 0))
                rejects(lambda: pos.request_credit_approval(partner.id, saved_order.uuid, 500, 500, 0, saved_order.id))
                saved_order.unlink()
                assert saved_order.exists()  # Preserve the record behind the approval link.
                new_ticket = Approval.browse(request_for(str(uuid4()), 500, 500)['id'])
                assert new_ticket.order_id != saved_order and new_ticket.state == 'pending'
                rejects(lambda: new_ticket.order_id._require_credit_approval(partner, 500, 0))
            # Cancelling an order after use records the cancellation without releasing approval.
            order.write({'state': 'cancel'})
            assert request.state == 'used' and request.used_order_id == order
            assert request.cancel_reason == 'Orden del PdV cancelada.'
            rejects(lambda: order._require_credit_approval(partner, 200, 0))
            assert env.ref('entintados_pdv.menu_pos_credit_approval').parent_id == env.ref('account.menu_finance_receivables')
            partner.is_credit = False
            rejects(lambda: pos.request_credit_approval(partner.id, str(uuid4()), 200, 200))
            print('PASS: persistent order link, reload lookup, cross-cashier reuse, legacy adoption, cancellation/reuse prevention, preserved approval/cancellation audit, department permissions, amount/risk checks and idempotent consumption. No invoice posted; full rollback.')
        finally:
            cr.rollback()


if __name__ == '__main__':
    run()
