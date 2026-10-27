import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../static/src/app/overrides/pos_financing.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture({ paid = 300, authorized = true, limit = 1000, approvalState = 'pending' } = {}) {
    const effects = [], timers = [], intervals = [], clearedIntervals = [];
    class Screen {
        setup() {}
        onMounted() { this.nativeMounted = true; }
        async toggleIsToInvoice() { this.currentOrder.setToInvoice(!this.currentOrder.to_invoice); }
        async validateOrder(force) { this.validated = { invoice: this.currentOrder.to_invoice, force }; }
        async addNewPaymentLine(method) { this.addedMethod = method; return true; }
    }
    class Controls {}
    vm.runInNewContext(source, { patch: (target, extension) => {
        Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
        Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
    }, PaymentScreen: Screen, ControlButtons: Controls,
        CustomSelectCreateDialog: class {}, _t: (value) => value,
        useEffect: (fn, dependencies) => effects.push({ fn, dependencies }),
        setTimeout: (fn) => { timers.push(fn); return timers.length; }, clearTimeout() {},
        setInterval: (fn, delay) => { intervals.push({ fn, delay }); return intervals.length; },
        clearInterval: (id) => clearedIntervals.push(id) });
    const cash = { id: 1, type: 'cash' };
    const deferred = { id: 2, type: 'pay_later' };
    const order = {
        uuid: 'test-ticket-uuid', uiState: {},
        lines: [{ qty: 1, price_unit: 1000, product_id: { id: 10, product_tmpl_id: {} } }],
        priceIncl: 1000, payment_ids: [], to_invoice: false, pos_payment_flow: 'standard',
        getPartner: () => ({ id: 7, is_credit: authorized }), setToInvoice(value) { this.to_invoice = value; },
        update(values) { Object.assign(this, values); },
        addPaymentline(method) { return { status: true, data: makePayment(method, 0) }; },
    };
    function makePayment(method, amount) {
        const line = { payment_method_id: method, amount,
            setAmount(value) { this.amount = value; },
            delete() { order.payment_ids.splice(order.payment_ids.indexOf(this), 1); } };
        order.payment_ids.push(line);
        return line;
    }
    if (paid) makePayment(cash, paid);
    const screen = new Screen();
    const notices = [];
    Object.assign(screen, { currentOrder: order, payment_methods_from_config: [cash, deferred],
        notification: { add: (value) => notices.push(value) },
        pos: { config: { id: 1, payment_method_ids: [cash, deferred] }, currency: { round: (value) => value },
            syncAllOrders: async () => { order.id = 42; order.state = 'draft'; },
            data: { call: async (model, method) => method === 'request_credit_approval'
                ? { id: 1, name: 'Autorización de prueba', state: approvalState }
                : { credit_authorized: authorized, credit_limited: true,
                    purchase_power: limit, risk_exception: false } } },
    });
    return { screen, order, notices, deferred, makePayment, effects, timers, intervals, clearedIntervals };
}

test('PPD keeps the actual receipt and puts only the remainder on account', async () => {
    const { screen, order } = fixture({ authorized: true });
    await screen.setDeferredPayment('ppd_partial');
    assert.equal(order.payment_ids[0].amount, 300);
    assert.equal(order.payment_ids[1].amount, 700);
    assert.equal(order.pos_payment_flow, 'ppd_partial');
    assert.equal(order.to_invoice, true);
});

test('PPD requires a real initial receipt', async () => {
    const { screen, order, notices } = fixture({ paid: 0 });
    await screen.setDeferredPayment('ppd_partial');
    assert.equal(order.payment_ids.length, 0);
    assert.equal(notices.length, 1);
});

test('Customers without Credit cannot use payment options or add on-account debt', async () => {
    for (const flow of ['ppd_partial', 'credit']) {
        const { screen, order, notices, deferred } = fixture({ authorized: false });
        assert.equal(screen.canOfferDeferredPayment, false);
        await screen.setDeferredPayment(flow);
        assert.equal(order.payment_ids.length, 1);
        assert.equal(order.pos_payment_flow, 'standard');
        assert.equal(await screen.addNewPaymentLine(deferred), false);
        assert.ok(notices.length >= 1);
        assert.equal(await screen.addNewPaymentLine({ type: 'cash' }), true);
    }
});

test('Changing to a customer without Credit blocks validation of an existing pending balance', async () => {
    const { screen, order } = fixture();
    await screen.setDeferredPayment('ppd_partial');
    order.getPartner = () => ({ id: 8, is_credit: false });
    assert.equal(await screen.validateOrder(), false);
    assert.equal(screen.validated, undefined);
});

test('Authorized credit can finance the entire sale without cash', async () => {
    const { screen, order } = fixture({ paid: 0 });
    await screen.setDeferredPayment('credit');
    assert.equal(order.payment_ids.length, 1);
    assert.equal(order.payment_ids[0].payment_method_id.type, 'pay_later');
    assert.equal(order.payment_ids[0].amount, 1000);
    assert.equal(order.pos_payment_flow, 'credit');
});

test('Explicit advance application reduces financing without subtracting it again from available credit', async () => {
    const { screen, order } = fixture({ limit: 600 });
    order.priceIncl = 800;
    order.lines.push({ price_unit: -200, qty: 1, advance_source_ref: 'invoice,10',
        product_id: { product_tmpl_id: {} }, prices: { total_included_currency: -200 } });
    await screen.setDeferredPayment('credit');
    assert.equal(order.payment_ids.at(-1).amount, 500);
    assert.equal(order.pos_payment_flow, 'credit');
    assert.equal(order.uiState.creditApproval, undefined);
});

test('Customer account button applies authorized credit to the unpaid balance without duplicate debt', async () => {
    const { screen, order, deferred } = fixture();
    order.invoice_split_count = 3;
    order.uiState.invoice_split_count = 3;
    assert.equal(await screen.addNewPaymentLine(deferred), true);
    assert.equal(order.invoice_split_count, 1);
    assert.equal(order.uiState.invoice_split_count, 1);
    assert.equal(order.pos_payment_flow, 'credit');
    assert.equal(order.to_invoice, true);
    assert.equal(order.payment_ids[0].amount, 300);
    assert.equal(order.payment_ids[1].amount, 700);
    assert.equal(await screen.addNewPaymentLine(deferred), true);
    assert.equal(order.payment_ids.length, 2);
    assert.equal(screen.addedMethod, undefined);
});

test('Customer account opens CFDI options before configuring PPD financing', async () => {
    const { screen, order, deferred } = fixture();
    screen.pos.company = { country_id: { code: 'MX' } };
    let opened = 0;
    screen.pos.addL10nMxEdiFields = async (ticket) => {
        opened++;
        assert.equal(ticket, order);
        assert.equal(ticket.uiState.creditInvoiceSetup, true);
        assert.equal(ticket.pos_payment_flow, 'standard');
        ticket.l10n_mx_edi_usage = 'G03';
        return true;
    };
    assert.equal(await screen.addNewPaymentLine(deferred), true);
    assert.equal(opened, 1);
    assert.equal(order.l10n_mx_edi_usage, 'G03');
    assert.equal(order.pos_payment_flow, 'credit');
    assert.equal(order.to_invoice, true);
    assert.equal(order.uiState.creditInvoiceSetup, undefined);
});

test('Cancelling CFDI options leaves credit and authorization unrequested', async () => {
    const { screen, order, deferred } = fixture({ limit: 100 });
    screen.pos.company = { country_id: { code: 'MX' } };
    screen.pos.addL10nMxEdiFields = async () => false;
    screen.pos.data.call = async () => { assert.fail('Credit must not be requested after cancelling the popup'); };
    assert.equal(await screen.addNewPaymentLine(deferred), false);
    assert.equal(order.payment_ids.length, 1);
    assert.equal(order.pos_payment_flow, 'standard');
    assert.equal(order.uiState.creditInvoiceSetup, undefined);
    assert.equal(screen.isMxEdiPopupOpen, false);
});

test('Customer account button requests approval and adds debt only after approval', async () => {
    for (const approvalState of ['pending', 'refused', 'approved']) {
        const { screen, order, deferred } = fixture({ limit: 100, approvalState });
        assert.equal(await screen.addNewPaymentLine(deferred), approvalState === 'approved');
        assert.equal(order.uiState.creditApproval.state, approvalState);
        assert.equal(order.payment_ids.length, approvalState === 'approved' ? 2 : approvalState === 'refused' ? 0 : 1);
        assert.equal(screen.addedMethod, undefined);
    }
});

test('Native debt settlement keeps its account-payment behavior', async () => {
    const { screen, order, deferred } = fixture();
    order.is_settling_account = true;
    assert.equal(await screen.addNewPaymentLine(deferred), true);
    assert.equal(screen.addedMethod, deferred);
    assert.equal(order.pos_payment_flow, 'standard');
});

test('Approval waits for draft synchronization and is linked to the database order id', async () => {
    const { screen, order, deferred } = fixture({ limit: 100 });
    const events = [];
    screen.pos.syncAllOrders = async (options) => {
        assert.equal(options.orders[0], order);
        assert.equal(options.throw, true);
        events.push('saved');
        order.id = 123;
    };
    const call = screen.pos.data.call;
    screen.pos.data.call = async (model, method, args) => {
        if (method === 'request_credit_approval') {
            events.push('requested');
            assert.equal(args[6], 123);
        }
        return call(model, method, args);
    };
    await screen.addNewPaymentLine(deferred);
    assert.deepEqual(events, ['saved', 'requested']);
});

test('A failed draft save never creates an approval or deferred debt', async () => {
    const { screen, order, deferred } = fixture({ limit: 100 });
    screen.pos.syncAllOrders = async () => { throw new Error('Offline'); };
    assert.equal(await screen.addNewPaymentLine(deferred), false);
    assert.equal(order.uiState.creditApproval, undefined);
    assert.equal(order.payment_ids.length, 1);
});

test('Removing the cash payment automatically updates the same authorization and removes outdated financed debt', async () => {
    const { screen, order, effects, timers } = fixture({ paid: 5000 });
    order.priceIncl = 9488.58;
    let requests = 0;
    screen.pos.data.call = async (model, method, args) => {
        if (method !== 'request_credit_approval') {
            return { credit_authorized: true, credit_limited: true, purchase_power: 100 };
        }
        requests++;
        return { id: 17, name: 'ACRED-0017', state: requests === 1 ? 'approved' : 'pending',
            credit_amount: args[4], total_amount: args[3], applied_amount: args[5],
            previous_credit_amount: 4488.58, revised: requests > 1 };
    };
    await screen.setDeferredPayment('credit');
    assert.equal(order.uiState.creditApproval.credit_amount, 4488.58);
    order.payment_ids[0].delete();
    assert.equal(screen.creditApprovalAmountsChanged, true);
    assert.equal(await screen.validateOrder(), false);
    screen.setup();
    effects[1].fn();
    await timers[0]();
    assert.equal(requests, 2);
    assert.equal(order.uiState.creditApproval.id, 17);
    assert.equal(order.uiState.creditApproval.credit_amount, 9488.58);
    assert.equal(order.uiState.creditApproval.state, 'pending');
    assert.equal(order.uiState.creditApproval.revised, true);
    assert.equal(order.payment_ids.length, 0);
    assert.equal(screen.creditApprovalAmountsChanged, false);
});

test('Reloading the payment screen restores approval from the persisted order', async () => {
    const { screen, order } = fixture();
    order.id = 123;
    screen.pos.data.call = async (model, method, args) => {
        assert.equal(method, 'get_order_credit_approval');
        assert.equal(args[1], 123);
        return { id: 5, name: 'ACRED-0005', state: 'approved' };
    };
    await screen.restoreCreditApproval();
    assert.equal(order.uiState.creditApproval.name, 'ACRED-0005');
    assert.equal(order.uiState.creditApproval.state, 'approved');
});

test('Approval polling automatically adds only the authorized remainder and never duplicates the payment', async () => {
    const { screen, order, effects, intervals, clearedIntervals } = fixture({ limit: 100 });
    order.id = 42;
    order.state = 'draft';
    order.uiState.creditApproval = { id: 17, state: 'pending', credit_amount: 700, total_amount: 1000 };
    let approvalsChecked = 0;
    screen.pos.data.call = async (model, method) => {
        if (method === 'get_advance_pos_info') {
            return { credit_authorized: true, credit_limited: true, purchase_power: 100 };
        }
        if (method === 'request_credit_approval') {
            approvalsChecked++;
        }
        return { id: 17, name: 'ACRED-0017', state: 'approved', credit_amount: 700, total_amount: 1000, applied_amount: 0 };
    };
    screen.setup();
    const cleanup = effects[2].fn();
    assert.equal(intervals[0].delay, 5000);
    await intervals[0].fn();
    assert.equal(order.pos_payment_flow, 'credit');
    assert.equal(order.payment_ids[0].amount, 300);
    assert.equal(order.payment_ids[1].amount, 700);
    assert.equal(order.to_invoice, true);
    assert.equal(screen.validated, undefined);
    await intervals[0].fn();
    assert.equal(order.payment_ids.length, 2);
    assert.equal(approvalsChecked, 1);
    cleanup();
    assert.deepEqual(clearedIntervals, [1]);
});

test('A temporary status lookup failure keeps the request available for the next automatic check', async () => {
    const { screen, order } = fixture();
    order.id = 42;
    const request = { id: 17, state: 'pending', credit_amount: 700 };
    order.uiState.creditApproval = request;
    screen.pos.data.call = async () => { throw new Error('Offline'); };
    await screen.restoreCreditApproval();
    assert.equal(order.uiState.creditApproval, request);
    assert.equal(order.payment_ids.length, 1);
});

test('Refreshing a rejected authorization automatically clears all entered payments', async () => {
    const { screen, order, deferred, makePayment } = fixture();
    order.id = 42;
    order.state = 'draft';
    order.pos_payment_flow = 'credit';
    order.uiState.creditApproval = { id: 17, state: 'pending', credit_amount: 700 };
    makePayment(deferred, 700);
    screen.pos.data.call = async () => ({ id: 17, name: 'ACRED-0017', state: 'refused', credit_amount: 700 });
    await screen.restoreCreditApproval();
    assert.equal(order.uiState.creditApproval.state, 'refused');
    assert.equal(order.payment_ids.length, 0);
    assert.equal(order.pos_payment_flow, 'standard');
});

test('Subsequent status checks do not erase new cash payments entered after the rejection was handled', async () => {
    const { screen, order } = fixture();
    order.id = 42;
    order.uiState.creditApproval = { id: 17, state: 'refused', credit_amount: 700 };
    screen.pos.data.call = async () => ({ id: 17, state: 'refused', credit_amount: 700 });
    await screen.restoreCreditApproval();
    assert.equal(order.payment_ids.length, 1);
    assert.equal(order.payment_ids[0].amount, 300);
});

test('Approval with an outdated amount is not automatically applied', async () => {
    const { screen, order } = fixture();
    order.uiState.creditApproval = { id: 17, state: 'approved', credit_amount: 500 };
    assert.equal(await screen.applyApprovedCredit(), false);
    assert.equal(order.payment_ids.length, 1);
});

test('Rejected credit lets the cashier clear every payment and return to standard payment', async () => {
    const { screen, order, deferred, makePayment } = fixture();
    order.id = 123;
    order.state = 'draft';
    order.pos_payment_flow = 'credit';
    order.uiState.creditApproval = { id: 17, name: 'ACRED-0017', state: 'refused', credit_amount: 700 };
    makePayment(deferred, 700);
    assert.equal(await screen.clearRejectedPayments(), true);
    assert.equal(order.payment_ids.length, 0);
    assert.equal(order.pos_payment_flow, 'standard');
    assert.equal(order.uiState.creditApproval.id, 17);
    assert.equal(order.uiState.creditApproval.state, 'refused');
    assert.equal(screen.creditApprovalAmountsChanged, false);
});

test('Clearing rejected credit does not silently erase a processed terminal payment', async () => {
    const { screen, order } = fixture();
    order.uiState.creditApproval = { state: 'refused' };
    order.payment_ids[0].payment_status = 'done';
    assert.equal(await screen.clearRejectedPayments(), false);
    assert.equal(order.payment_ids.length, 1);
});

test('Editing payments after refusal does not automatically reopen the rejected request', () => {
    const { screen, order, effects, timers } = fixture();
    order.uiState.creditApproval = { id: 17, state: 'refused', credit_amount: 700 };
    order.payment_ids[0].setAmount(0);
    screen.setup();
    effects[1].fn();
    assert.equal(screen.creditApprovalAmountsChanged, false);
    assert.equal(timers.length, 0);
});

test('Clicking customer account explicitly requests a new attempt after refusal', async () => {
    const { screen, order, deferred } = fixture({ limit: 100 });
    order.uiState.creditApproval = { id: 17, state: 'refused', credit_amount: 700 };
    const call = screen.pos.data.call;
    screen.pos.data.call = async (model, method, args) => {
        if (method === 'request_credit_approval') {
            assert.equal(args[7], true);
            return { id: 18, name: 'ACRED-0018', state: 'pending', credit_amount: 700 };
        }
        return call(model, method, args);
    };
    assert.equal(await screen.addNewPaymentLine(deferred), false);
    assert.equal(order.uiState.creditApproval.id, 18);
    assert.equal(order.uiState.creditApproval.state, 'pending');
});

test('Credit refuses missing authorization and insufficient available credit', async () => {
    for (const options of [{ authorized: false }, { limit: 600 }]) {
        const { screen, order, notices } = fixture(options);
        await screen.setDeferredPayment('credit');
        assert.equal(order.payment_ids.length, 1);
        assert.equal(notices.length, 1);
    }
});

test('Over-limit credit requests approval and leaves the sale unpaid while pending or refused', async () => {
    for (const approvalState of ['pending', 'refused']) {
        const { screen, order } = fixture({ limit: 100, approvalState });
        await screen.setDeferredPayment('credit');
        assert.equal(order.uiState.creditApproval.state, approvalState);
        assert.equal(order.payment_ids.length, approvalState === 'refused' ? 0 : 1);
        assert.equal(order.pos_payment_flow, 'standard');
    }
});

test('Department approval enables the requested over-limit financing', async () => {
    const { screen, order } = fixture({ limit: 100, approvalState: 'approved' });
    await screen.setDeferredPayment('credit');
    assert.equal(order.payment_ids.length, 2);
    assert.equal(order.payment_ids[1].amount, 700);
    assert.equal(order.pos_payment_flow, 'credit');
});

test('Repeated selection does not duplicate deferred debt; full payment preserves real receipts', async () => {
    const { screen, order } = fixture();
    await screen.setDeferredPayment('ppd_partial');
    await screen.setDeferredPayment('ppd_partial');
    assert.equal(order.payment_ids.length, 2);
    screen.clearDeferredPayment();
    assert.equal(order.payment_ids.length, 1);
    assert.equal(order.payment_ids[0].amount, 300);
    assert.equal(order.pos_payment_flow, 'standard');
});

test('Creating reusable customer credit requires an actual payment, not financing', async () => {
    const { screen, order, notices } = fixture();
    order.lines[0].product_id.product_tmpl_id.is_accounting_advance = true;
    assert.equal(screen.canOfferDeferredPayment, false);
    await screen.setDeferredPayment('credit');
    assert.equal(order.payment_ids.length, 1);
    assert.equal(notices.length, 1);
});

test('Financing options are offered for sales, never for empty tickets or native down payments', () => {
    const { screen, order } = fixture();
    assert.equal(screen.canOfferDeferredPayment, true);
    screen.pos.config.down_payment_product_id = { id: 10 };
    assert.equal(screen.canOfferDeferredPayment, false);
    order.lines = [];
    order.priceIncl = 0;
    assert.equal(screen.canOfferDeferredPayment, false);
});

test('Advance invoices are restored on payment entry and cannot be switched off', async () => {
    const { screen, order, notices } = fixture();
    order.lines[0].product_id.product_tmpl_id.is_accounting_advance = true;
    screen.onMounted();
    assert.equal(screen.nativeMounted, true);
    assert.equal(order.to_invoice, true);
    await screen.toggleIsToInvoice();
    assert.equal(order.to_invoice, true);
    assert.equal(notices.length, 1);
});

test('Validation repairs old advances and applications before native payment validation', async () => {
    for (const kind of ['customer', 'application', 'sale']) {
        const { screen, order } = fixture();
        if (kind === 'customer') order.lines[0].product_id.product_tmpl_id.is_accounting_advance = true;
        if (kind === 'application') order.lines[0].advance_source_ref = 42;
        if (kind === 'sale') {
            screen.pos.config.advance_risk_enabled = true;
            screen.pos.config.down_payment_product_id = { id: 10 };
            order.lines[0].sale_order_origin_id = { id: 5 };
        }
        await screen.validateOrder(true);
        assert.equal(order.to_invoice, true);
        assert.deepEqual(screen.validated, { invoice: true, force: true });
    }
});

test('Ordinary sales retain the native optional invoice behavior', async () => {
    const { screen, order } = fixture();
    screen.onMounted();
    assert.equal(order.to_invoice, false);
    await screen.validateOrder(false);
    assert.deepEqual(screen.validated, { invoice: false, force: false });
    await screen.toggleIsToInvoice();
    assert.equal(order.to_invoice, true);
    await screen.toggleIsToInvoice();
    assert.equal(order.to_invoice, false);
});

test('Receiving customer funds hides and rejects on-account debt but accepts cash', async () => {
    const { screen, order, deferred, notices } = fixture();
    order.lines[0].product_id.product_tmpl_id.is_accounting_advance = true;
    screen.onMounted();
    assert.equal(screen.payment_methods_from_config.length, 1);
    assert.equal(screen.payment_methods_from_config[0].type, 'cash');
    assert.equal(await screen.addNewPaymentLine(deferred), false);
    assert.equal(screen.addedMethod, undefined);
    assert.equal(notices.length, 1);
    assert.equal(await screen.addNewPaymentLine(screen.payment_methods_from_config[0]), true);
    assert.equal(screen.addedMethod.type, 'cash');
});
