import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../static/src/app/overrides/advance_controls.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture({ partner = true, cart = 500, replies = [], available = 300, networkError = false, tax = false } = {}) {
    class Controls {}
    const template = { id: 9, is_accounting_advance: true };
    const order = {
        lines: cart ? [{ qty: 1, price_unit: cart, prices: { total_included_currency: cart } }] : [],
        payment_ids: [], to_invoice: false,
        getPartner: () => partner ? { id: 8 } : false,
        setToInvoice(value) { this.to_invoice = value; },
        get priceIncl() { return this.lines.reduce((total, line) => total + line.prices.total_included_currency, 0); },
    };
    const advance = { id: 10, name: 'ADV/001', available, total: 300, fiscal_kind: 'on_account',
        lines: [{ quantity: 1, price_unit: 300, discount: 0, tax_ids: [] }] };
    const notices = [];
    let requests = 0;
    let currentOrder = order;
    const pos = {
        getOrder: () => currentOrder,
        addNewOrder() {
            currentOrder = { lines: [], payment_ids: [],
                setPartner(partner) { this.partner = partner; },
                setToInvoice(value) { this.to_invoice = value; } };
            return currentOrder;
        },
        config: { id: 4, advance_risk_enabled: true, advance_product_id: { id: 99, product_tmpl_id: {} }, down_payment_product_id: { id: 7, product_tmpl_id: template } },
        env: { utils: { formatCurrency: String } }, models: { 'account.tax': { get: (id) => tax && tax.id === id ? tax : undefined } },
        data: { async call(model, method, args) {
            requests++;
            assert.equal(model, 'res.partner');
            assert.equal(method, 'get_advance_pos_info');
            assert.deepEqual(JSON.parse(JSON.stringify(args)), [[8], 4]);
            if (networkError) throw new Error('Offline');
            return { advance_available: available, purchase_power: 1300, credit_limited: true, sources: [advance] };
        } },
        async addLineToCurrentOrder(values) {
            const rate = (values.tax_ids || []).reduce((sum, command) => {
                assert.equal(command[0], 'link');
                return sum + command[1].amount / 100;
            }, 0);
            const line = { ...values, prices: { total_included_currency: values.price_unit * values.qty * (1 + rate) } };
            currentOrder.lines.push(line);
            return line;
        },
    };
    const context = vm.createContext({
        patch: (target, extension) => Object.assign(target, extension),
        ControlButtons: Controls, NumberPopup: class {}, SelectionPopup: class {},
        _t: (value) => value,
        makeAwaitable: async () => {
            const result = replies.shift();
            return result === 'source' ? advance : result;
        },
    });
    vm.runInContext(source, context);
    const controls = new Controls();
    Object.assign(controls, { pos, dialog: {}, notification: { add: (...args) => notices.push(args) } });
    return { controls, order, notices, advance, currentOrder: () => currentOrder, requests: () => requests };
}

test('Collecting an advance creates an invoice line and no second cash payment', async () => {
    const f = fixture({ cart: 0, replies: ['collect', '300'] });
    await f.controls.onClickAdvances();
    assert.equal(f.order.lines.length, 1);
    assert.equal(f.order.lines[0].price_unit, 300);
    assert.equal(f.order.to_invoice, true);
    assert.equal(f.order.payment_ids.length, 0);
});

test('Receiving an advance from the menu uses the generic product without merchandise', async () => {
    const f = fixture({ cart: 0, replies: ['collect', '250'] });
    await f.controls.onClickAdvances();
    assert.equal(f.order.lines.length, 1);
    assert.equal(f.order.lines[0].product_id.id, 7);
    assert.equal(f.order.lines[0].qty, 1);
    assert.equal(f.order.lines[0].price_unit, 250);
    assert.equal(f.order.to_invoice, true);
    assert.equal(f.notices.length, 0);
});

test('Unconfigured advances explain the missing setup instead of opening a sales order', async () => {
    const f = fixture({ cart: 0, replies: ['250'] });
    f.controls.pos.config.advance_risk_enabled = false;
    await f.controls.onClickAdvances();
    assert.equal(f.requests(), 0);
    assert.equal(f.order.lines.length, 0);
    assert.match(f.notices[0][0], /no están configurados/);
});

test('Direct advance sale preserves merchandise and reuses the same generic product', async () => {
    const f = fixture({ cart: 500, replies: ['250'] });
    await f.controls.onClickAdvances('collect');
    assert.equal(f.order.lines.length, 1);
    assert.equal(f.order.priceIncl, 500);
    assert.notEqual(f.currentOrder(), f.order);
    assert.equal(f.currentOrder().lines[0].product_id.id, 7);
    assert.equal(f.currentOrder().lines[0].price_unit, 250);
    assert.equal(f.currentOrder().partner.id, 8);
});

test('Applying an advance reduces the sale amount without inventing a payment', async () => {
    const f = fixture({ replies: ['apply', 'source', '100'] });
    await f.controls.onClickAdvances();
    assert.equal(f.order.priceIncl, 400);
    assert.equal(f.order.lines.at(-1).advance_source_ref, 10);
    assert.equal(f.order.to_invoice, true);
    assert.equal(f.order.payment_ids.length, 0);
});

test('Collecting credit preserves an existing sale or an empty ticket with payments', async () => {
    for (const cart of [0, 500]) {
        const f = fixture({ cart, replies: ['collect', '300'] });
        f.order.payment_ids.push({ amount: 5000 });
        await f.controls.onClickAdvances();
        assert.notEqual(f.currentOrder(), f.order);
        assert.equal(f.order.payment_ids[0].amount, 5000);
        assert.equal(f.order.lines.length, cart ? 1 : 0);
        assert.equal(f.currentOrder().partner.id, 8);
        assert.equal(f.currentOrder().lines[0].price_unit, 300);
        assert.equal(f.currentOrder().to_invoice, true);
        assert.equal(f.currentOrder().payment_ids.length, 0);
        assert.equal(f.notices.length, 0);
    }
});

test('Cancelling the amount leaves the original ticket selected', async () => {
    const f = fixture({ replies: ['collect', undefined] });
    await f.controls.onClickAdvances();
    assert.equal(f.currentOrder(), f.order);
    assert.equal(f.order.lines.length, 1);
});

test('The current cart reservation cannot spend the same balance a second time', async () => {
    const f = fixture({ available: 150, replies: ['apply', 'source', '100'] });
    f.order.lines.push({ qty: 1, price_unit: -100, advance_source_ref: 10, prices: { total_included_currency: -100 } });
    await f.controls.onClickAdvances();
    assert.equal(f.order.lines.length, 2);
    assert.equal(f.notices.length, 1);
});

test('Missing tax data aborts before adding any deduction to the cart', async () => {
    const f = fixture({ replies: ['apply', 'source', '100'] });
    f.advance.lines.push({ quantity: 1, price_unit: 20, discount: 0, tax_ids: [99] });
    await f.controls.onClickAdvances();
    assert.equal(f.order.lines.length, 1);
    assert.equal(f.notices.length, 1);
});

test('A cash advance requires a customer and fresh server availability', async () => {
    const anonymous = fixture({ partner: false, replies: ['collect', '100'] });
    await anonymous.controls.onClickAdvances();
    assert.equal(anonymous.requests(), 0);
    const offline = fixture({ networkError: true, replies: ['apply', 'source', '100'] });
    await offline.controls.onClickAdvances();
    assert.equal(offline.order.lines.length, 1);
    assert.equal(offline.notices.length, 1);
});

test('Taxed advance uses native POS relation commands and deducts the gross amount', async () => {
    const f = fixture({ replies: ['apply', 'source', '116'], tax: { id: 1, amount: 16, amount_type: 'percent' } });
    f.advance.total = f.advance.available = 116;
    f.advance.lines = [{ quantity: 1, price_unit: 100, discount: 0, tax_ids: [1] }];
    await f.controls.onClickAdvances();
    assert.equal(f.notices.length, 0);
    assert.equal(f.order.priceIncl, 384);
});
