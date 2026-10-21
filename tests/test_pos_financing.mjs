import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../static/src/app/overrides/pos_financing.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture({ paid = 300, authorized = true, limit = 1000 } = {}) {
    class Screen {
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
        CustomSelectCreateDialog: class {}, _t: (value) => value });
    const cash = { id: 1, type: 'cash' };
    const deferred = { id: 2, type: 'pay_later' };
    const order = {
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
            data: { call: async () => ({ credit_authorized: authorized, credit_limited: true,
                purchase_power: limit, risk_exception: false }) } },
    });
    return { screen, order, notices, deferred, makePayment };
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

test('Credit refuses missing authorization and insufficient available credit', async () => {
    for (const options of [{ authorized: false }, { limit: 600 }]) {
        const { screen, order, notices } = fixture(options);
        await screen.setDeferredPayment('credit');
        assert.equal(order.payment_ids.length, 1);
        assert.equal(notices.length, 1);
    }
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
