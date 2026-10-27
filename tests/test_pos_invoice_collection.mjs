import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
const partner = { name: 'Cliente de prueba', commercial_partner_id: { id: 7 } };

const source = fs.readFileSync(new URL('../static/src/app/overrides/pos_invoice_collection.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
function fixture(prices, paid) {
    class Validation { async settleOrderDues() {
        this.nativeCalled = true;
        this.requested = this.order.getSettleAmount();
        if (this.cancelled) return false;
        this.order.payment_ids.push({ payment_method_id: { type: 'pay_later' }, amount: -this.requested });
        return true;
    }
        async validateOrder() {
            this.requested = this.order.getSettleAmount();
            this.validated = true;
            return true;
        }
    }
    vm.runInNewContext(source, {
        OrderPaymentValidation: Validation, AlertDialog: class {},
        _t: (value, ...args) => value.replace(/%s/g, () => String(args.shift())),
        ask: async (_dialog, options) => { validation.confirmation = options; return !validation.cancelled; },
        patch(target, extension) {
            Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        },
    });
    const lines = prices.map((amount, index) => ({
        settled_invoice_id: { id: index + 1, name: `INV/TEST/${index + 1}` }, amount, price_unit: amount, qty: 1,
        get prices() { return { total_included: this.amount }; },
        setUnitPrice(value) { this.amount = value; this.price_unit = value; },
    }));
    const order = { lines, to_invoice: true, pos_payment_flow: 'credit', commercialPartnerId: 7,
        payment_ids: [{ amount: paid, payment_method_id: { type: 'bank' } }],
        getSettleAmount() { return this.lines.reduce((sum, line) => sum + line.amount, 0); },
        setToInvoice(value) { this.to_invoice = value; },
        addPaymentline() {
            const payment = { payment_method_id: { type: 'pay_later' }, setAmount(value) { this.amount = value; } };
            this.payment_ids.push(payment);
            return { status: true, data: payment };
        },
    };
    const validation = new Validation();
    validation.order = order;
    validation.pos = { dialog: { add() { validation.error = true; } },
        env: { utils: { formatCurrency: (amount) => '$' + amount.toFixed(2) } } };
    return { validation, order, lines };
}
test('partial receipt settles only the money actually received and disables new invoice', async () => {
    const { validation, order, lines } = fixture([1000], 300);
    assert.equal(await validation.settleOrderDues(partner, {}, lines), true);
    assert.equal(validation.requested, 300);
    assert.equal(order.to_invoice, false);
    assert.equal(order.pos_payment_flow, 'standard');
    assert.equal(validation.confirmation.title, 'Confirmar abono');
    assert.equal(validation.confirmation.body, '¿Desea abonar $300.00 a la factura INV/TEST/1 del cliente Cliente de prueba?');
    assert.equal(order.payment_ids.filter((line) => line.payment_method_id.type === 'pay_later').length, 1);
});
test('multiple invoice collection distributes partial receipt without exceeding selected amounts', async () => {
    const { validation, lines } = fixture([200, 600], 450);
    await validation.settleOrderDues(partner, {}, lines);
    assert.deepEqual(lines.map((line) => line.amount), [200, 250]);
    assert.match(validation.confirmation.body, /¿Desea abonar \$450.00 a las facturas INV\/TEST\/1, INV\/TEST\/2/);
});
test('overpayment keeps invoice amount and leaves native validation to return change', async () => {
    const { validation, lines } = fixture([1000], 1500);
    await validation.settleOrderDues(partner, {}, lines);
    assert.equal(validation.requested, 1000);
});
test('mixing invoice collection with products is rejected', async () => {
    const { validation, order, lines } = fixture([1000], 1000);
    order.lines.push({ amount: 10 });
    assert.equal(await validation.settleOrderDues(partner, {}, lines), false);
    assert.equal(validation.nativeCalled, undefined);
});
test('zero receipt does not generate debt or a collection', async () => {
    const { validation, lines } = fixture([1000], 0);
    assert.equal(await validation.settleOrderDues(partner, {}, lines), false);
    assert.equal(validation.nativeCalled, undefined);
});
test('cancelling confirmation restores the originally selected amount', async () => {
    const { validation, order, lines } = fixture([1000], 300);
    validation.cancelled = true;
    assert.equal(await validation.settleOrderDues(partner, {}, lines), false);
    assert.equal(lines[0].amount, 1000);
    assert.equal(order.payment_ids.length, 1);
});
