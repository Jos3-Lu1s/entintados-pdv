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
    Object.assign(store.config, { id: 1, advance_product_id: { id: 99 }, down_payment_product_id: { id: 9, product_tmpl_id: {} } });
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

test('Pay never adds available advances or consumes unselected balances', async () => {
    const store = autoFixture(5000, 1500);
    store.data.call = async () => { throw new Error('Pay must not look up unselected advances'); };
    for (let visit = 0; visit < 3; visit++) await store.pay();
    assert.equal(store.order.priceIncl, 1500);
    assert.equal(store.order.lines.length, 1);
    assert.equal(store.payCalls, 3);
});

test('Pay preserves the selected source and partial amount even when other advances exist', async () => {
    const store = autoFixture(5000, 1500);
    store.order.lines.push({ price_unit: -200, qty: 1, advance_source_ref: 77,
        product_id: store.config.down_payment_product_id });
    store.data.call = async () => { throw new Error('Other advances must not be selected automatically'); };
    for (let visit = 0; visit < 3; visit++) await store.pay();
    assert.equal(store.order.lines.length, 2);
    assert.equal(store.order.lines[1].advance_source_ref, 77);
    assert.equal(store.order.lines[1].price_unit, -200);
    assert.equal(store.order.priceIncl, 1300);
    assert.equal(store.payCalls, 3);
});

test('Removing a selected advance keeps it removed on repeated payment visits', async () => {
    const store = autoFixture(5000, 1500);
    store.order.lines.push({ price_unit: -200, qty: 1, advance_source_ref: 77 });
    store.order.lines.splice(1, 1);
    for (let visit = 0; visit < 3; visit++) await store.pay();
    assert.equal(store.order.priceIncl, 1500);
    assert.equal(store.order.lines.length, 1);
    assert.equal(store.payCalls, 3);
});

test('Customer refresh replaces the displayed advance balance after purchase', async () => {
    const store = fixture();
    const partner = { id: 8, customer_advance_available: 5000 };
    store.models = { 'res.partner': { get: () => partner } };
    await store.refreshTotalDueOfPartner(partner);
    assert.equal(partner.customer_advance_available, 3500);
});
