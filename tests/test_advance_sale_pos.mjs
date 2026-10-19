import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/overrides/advance_sale_pos.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture() {
    class Store {
        async refreshTotalDueOfPartner(partner) { return [{ id: partner.id, customer_advance_available: 3500 }]; }
        async pay() { this.payCalls = (this.payCalls || 0) + 1; }
        async addDownPaymentProductOrderlineToOrder(...args) { this.nativeCalls.push(args); }
        async addLineToCurrentOrder() { return { order_id: this.order }; }
    }
    vm.runInNewContext(source, {
        PosStore: Store, _t: (text) => text,
        patch(target, extension) {
            Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        },
    });
    const store = new Store();
    store.nativeCalls = [];
    store.notices = [];
    store.notification = { add: (message) => store.notices.push(message) };
    store.config = { advance_risk_enabled: true, down_payment_product_id: { id: 7 } };
    store.order = { to_invoice: false, setToInvoice(value) { this.to_invoice = value; } };
    return store;
}

test('Uses the native calculation for percentage and fixed deposits', async () => {
    const store = fixture();
    const sale = { amount_unpaid: 700 };
    await store.addDownPaymentProductOrderlineToOrder(sale, 30, true);
    await store.addDownPaymentProductOrderlineToOrder(sale, 200, false);
    assert.deepEqual(store.nativeCalls, [[sale, 30, true], [sale, 200, false]]);
});

test('Rejects percentages over 100 and deposits above the unpaid balance', async () => {
    const store = fixture();
    await store.addDownPaymentProductOrderlineToOrder({ amount_unpaid: 700 }, 101, true);
    await store.addDownPaymentProductOrderlineToOrder({ amount_unpaid: 700 }, 701, false);
    assert.equal(store.nativeCalls.length, 0);
    assert.equal(store.notices.length, 2);
});

test('Keeps native final settlement deductions and requires an invoice for the deposit', async () => {
    const store = fixture();
    await store.addDownPaymentProductOrderlineToOrder({ amount_unpaid: 700 }, -300, false);
    assert.equal(store.nativeCalls.length, 1);
    await store.addLineToCurrentOrder({ product_id: { id: 7 }, sale_order_origin_id: { id: 42 } });
    assert.equal(store.order.to_invoice, true);
});

function autoFixture(balance, total) {
    const store = fixture();
    const partner = { id: 8 };
    Object.assign(store.config, { id: 1, advance_product_id: { id: 9, product_tmpl_id: {} } });
    Object.assign(store.order, { lines: [{ price_unit: total, qty: 1 }], payment_ids: [], getPartner: () => partner });
    Object.defineProperty(store.order, 'priceIncl', { get() {
        return this.lines.reduce((sum, line) => sum + line.price_unit * line.qty, 0);
    } });
    store.getOrder = () => store.order;
    store.data = { call: async () => ({ sources: [{ id: 42, total: balance, available: balance,
        fiscal_kind: 'on_account', lines: [{ price_unit: balance, quantity: 1, discount: 0, tax_ids: [] }] }] }) };
    store.models = { 'account.tax': { get: () => undefined } };
    store.addLineToCurrentOrder = async (values) => {
        const line = { ...values, prices: { total_included_currency: values.price_unit * values.qty } };
        store.order.lines.push(line);
        return line;
    };
    return store;
}

test('Pay automatically uses customer funds and leaves only the difference due', async () => {
    for (const [balance, total, due] of [[5000, 1500, 0], [500, 1500, 1000]]) {
        const store = autoFixture(balance, total);
        await store.pay();
        assert.equal(store.order.priceIncl, due);
        assert.equal(store.order.lines[1].advance_source_ref, 42);
        assert.equal(store.order.to_invoice, true);
        assert.equal(store.order.payment_ids.length, 0);
        assert.equal(store.payCalls, 1);
        await store.pay();
        assert.equal(store.order.priceIncl, due);
        assert.equal(store.order.lines.length, 2);
    }
});

test('Receiving new funds and paying existing invoices do not spend advances automatically', async () => {
    const store = autoFixture(5000, 1500);
    store.order.lines[0].product_id = { product_tmpl_id: { is_accounting_advance: true } };
    await store.pay();
    assert.equal(store.order.lines.length, 1);
    assert.equal(store.payCalls, 1);
});

test('Offline balance lookup prevents collecting the full sale without checking customer funds', async () => {
    const store = autoFixture(5000, 1500);
    store.data.call = async () => { throw new Error('Offline'); };
    await store.pay();
    assert.equal(store.payCalls, undefined);
    assert.equal(store.order.lines.length, 1);
    assert.equal(store.notices.length, 1);
});

test('Customer refresh replaces the displayed advance balance after purchase', async () => {
    const store = fixture();
    const partner = { id: 8, customer_advance_available: 5000 };
    store.models = { 'res.partner': { get: () => partner } };
    await store.refreshTotalDueOfPartner(partner);
    assert.equal(partner.customer_advance_available, 3500);
});
