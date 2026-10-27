import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../static/src/app/overrides/add_info_popup.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function popupFor(values = {}) {
    class Popup {
        setup() { this.state = {}; }
        confirm() { this.payload = { ...this.state }; }
    }
    vm.runInNewContext(source, { AddInfoPopup: Popup, patch(target, extension) {
        Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
        Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
    } });
    const popup = new Popup();
    popup.props = { order: { uiState: {}, invoice_split_count: 3, payment_ids: [], ...values } };
    popup.setup();
    return popup;
}

test('Cash/PUE invoices retain the selected number of invoices', () => {
    const popup = popupFor({ l10n_mx_edi_payment_policy: 'PUE' });
    assert.equal(popup.canSplitInvoice, true);
    popup.confirm();
    assert.equal(popup.payload.invoice_split_count, 3);
    assert.equal(popup.props.order.invoice_split_count, 3);
});

test('PPD and customer-account sales hide splitting and discard old split counts', () => {
    for (const values of [
        { pos_payment_flow: 'credit' }, { pos_payment_flow: 'ppd_partial' },
        { uiState: { invoice_split_count: 4, creditInvoiceSetup: true } },
        { l10n_mx_edi_payment_policy: 'PPD' },
        { payment_ids: [{ amount: 100, payment_method_id: { type: 'pay_later' } }] },
    ]) {
        const popup = popupFor({ uiState: { invoice_split_count: 4 }, ...values });
        assert.equal(popup.canSplitInvoice, false);
        popup.confirm();
        assert.equal(popup.payload.invoice_split_count, 1);
        assert.equal(popup.props.order.invoice_split_count, 1);
        assert.equal(popup.props.order.uiState.invoice_split_count, 1);
    }
});

test('Changing to credit while the popup is open confirms just one invoice', () => {
    const popup = popupFor();
    popup.props.order.pos_payment_flow = 'credit';
    popup.confirm();
    assert.equal(popup.payload.invoice_split_count, 1);
});

test('Saving a ticket explicitly replaces an old split count with one invoice', () => {
    class Order { serializeForORM() { return { invoice_split_count: 4 }; } }
    const orderSource = fs.readFileSync(new URL('../static/src/app/overrides/pos_order_line.js', import.meta.url), 'utf8')
        .replace(/^import .*;\r?\n/gm, '');
    vm.runInNewContext(orderSource, {
        PosOrder: Order, PosOrderline: class {}, _t: (value) => value,
        console: { log() {} },
        patch(target, extension) {
            Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        },
    });
    const order = new Order();
    order.uiState = { invoice_split_count: 1 };
    order.invoice_split_count = 1;
    assert.equal(order.serializeForORM().invoice_split_count, 1);
    order.uiState.invoice_split_count = 3;
    assert.equal(order.serializeForORM().invoice_split_count, 3);
});
