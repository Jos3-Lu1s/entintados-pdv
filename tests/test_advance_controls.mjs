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
    const pos = {
        getOrder: () => order, config: { id: 4, advance_product_id: { id: 7, product_tmpl_id: template } },
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
            order.lines.push(line);
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
    return { controls, order, notices, advance, requests: () => requests };
}

test('Collecting an advance creates an invoice line and no second cash payment', async () => {
    const f = fixture({ cart: 0, replies: ['collect', '300'] });
    await f.controls.onClickAdvances();
    assert.equal(f.order.lines.length, 1);
    assert.equal(f.order.lines[0].price_unit, 300);
    assert.equal(f.order.to_invoice, true);
    assert.equal(f.order.payment_ids.length, 0);
});

test('Applying an advance reduces the sale amount without inventing a payment', async () => {
    const f = fixture({ replies: ['apply', 'source', '100'] });
    await f.controls.onClickAdvances();
    assert.equal(f.order.priceIncl, 400);
    assert.equal(f.order.lines.at(-1).advance_source_ref, 10);
    assert.equal(f.order.to_invoice, true);
    assert.equal(f.order.payment_ids.length, 0);
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
