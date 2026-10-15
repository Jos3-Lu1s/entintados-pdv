import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/overrides/advance_sale_pos.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture() {
    class Store {
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
