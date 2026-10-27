import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../static/src/app/overrides/pos_debt_line_removal.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
function fixture() {
    class Line { get canBeRemoved() { return false; } }
    class Order {
        removeOrderline(line) { this.lines.splice(this.lines.indexOf(line), 1); return true; }
        getSelectedOrderline() { return this.lines[0]; }
    }
    class Summary {
        _setValue() { this.nativeCalls = (this.nativeCalls || 0) + 1; }
        async updateSelectedOrderline() { this.nativeCalls = (this.nativeCalls || 0) + 1; }
    }
    vm.runInNewContext(source, { PosOrderline: Line, PosOrder: Order, OrderSummary: Summary,
        patch(target, extension) {
            Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        },
    });
    const order = Object.assign(new Order(), { state: 'draft', lines: [], payment_ids: [], is_settling_account: true });
    for (const id of [1, 2]) order.lines.push(Object.assign(new Line(), {
        settled_invoice_id: { id }, order_id: order, qty: 1, price_unit: id * 100,
    }));
    const summary = Object.assign(new Summary(), { currentOrder: order, numberBuffer: { reset() {} } });
    function payment(type, amount) {
        const value = { payment_method_id: { type }, amount,
            delete() { order.payment_ids.splice(order.payment_ids.indexOf(value), 1); } };
        order.payment_ids.push(value);
        return value;
    }
    return { order, summary, payment, Line };
}
test('Backspace removes only the selected invoice before payment', async () => {
    const { order, summary } = fixture();
    const remaining = order.lines[1];
    assert.equal(order.lines[0].canRemoveDebtCollection, true);
    await summary.updateSelectedOrderline({ key: 'Backspace', buffer: null });
    assert.deepEqual(order.lines, [remaining]);
    assert.equal(summary.nativeCalls, undefined);
});
test('returning from payment removes the stale account counter-entry and restores the remaining amount', () => {
    const { order, summary, payment } = fixture();
    order.lines.forEach((line) => { line.qty = 0; });
    const cash = payment('cash', 300);
    payment('pay_later', -300);
    summary._setValue('remove');
    assert.equal(order.lines.length, 1);
    assert.equal(order.lines[0].qty, 1);
    assert.equal(order.lines[0].price_unit, 200);
    assert.deepEqual(order.payment_ids, [cash]);
});
test('removing the last invoice cannot turn the old collection into a customer deposit', () => {
    const { order, summary } = fixture();
    summary._setValue('remove');
    summary._setValue('remove');
    assert.equal(order.lines.length, 0);
    assert.equal(order.is_settling_account, false);
});
test('paid, processed and terminal-in-progress collections remain protected', () => {
    for (const state of [{ state: 'done' }, { finalized: true }, { invoice_collection_processed: true },
        { electronicPaymentInProgress: () => true }]) {
        const { order } = fixture();
        Object.assign(order, state);
        assert.equal(order.lines[0].canRemoveDebtCollection, false);
        assert.equal(order.removeOrderline(order.lines[0]), false);
        assert.equal(order.lines.length, 2);
    }
});
test('ordinary product lines keep their native removal behavior', () => {
    const { order, Line } = fixture();
    const product = Object.assign(new Line(), { order_id: order });
    order.lines.push(product);
    assert.equal(product.canBeRemoved, false);
    assert.equal(order.removeOrderline(product), true);
    assert.equal(order.lines.length, 2);
});
test('native payment cleanup never automatically removes the invoices being collected', () => {
    const { order } = fixture();
    order.lines.forEach((line) => { line.qty = 0; });
    assert.equal(order.lines.filter((line) => line.canBeRemoved).length, 0);
    assert.equal(order.lines.length, 2);
});
