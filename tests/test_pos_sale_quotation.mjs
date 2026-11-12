import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/overrides/pos_sale_quotation.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture(fail = false) {
    class ControlButtons {}
    class NativePosStore {
        pay() {
            assert.ok(this.getOrder().lines.some((line) => line.refunded_orderline_id));
            return 'payment';
        }
    }
    class PosStore extends NativePosStore {}
    vm.runInNewContext(source, { ControlButtons, PosStore, PosOrder: class {}, PaymentScreen: class {}, useService() {}, ask: async () => true,
        _t: (text) => text, patch: (target, extension) => {
            Object.setPrototypeOf(extension, Object.getPrototypeOf(target));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        } });
    const calls = [];
    const order = { id: 'local', uuid: 'ticket', state: 'draft', payment_ids: [],
        getPartner: () => ({ id: 1 }), lines: [{ uuid: 'line', product_id: { id: 2 }, qty: 1, price_unit: 100, discount: 0 }],
        setOrderPrices() { calls.push('prices'); this.lines[0].price_unit = 120; },
        serializeForORM() { return { uuid: this.uuid, state: this.state, price: this.lines[0].price_unit }; } };
    const button = new ControlButtons();
    Object.assign(button, { dialog: {}, props: {}, notification: { add() {} },
        quotationUI: { block() {}, unblock() { calls.push('unblock'); } },
        pos: { getOrder: () => order, getSyncAllOrdersContext: () => ({ config_id: 1 }),
            data: { call: async (model, method, args) => {
                calls.push({ model, method, args });
                if (fail) throw new Error('No connection');
                return { id: 42, name: 'S00042' };
            } },
            removePendingOrder() { calls.push('removePending'); }, removeOrder() { calls.push('remove'); },
            navigate() {}, addNewOrder() {} } });
    return { button, calls, PosStore };
}

test('loads missing original lines before the native refund payment validation', async () => {
    const { PosStore } = fixture();
    const store = new PosStore();
    const order = { id: 10, uuid: 'refund', isRefund: true, lines: [{}] };
    const calls = [];
    store.getOrder = () => order;
    store.data = { async callRelated(model, method) {
        calls.push(method);
        if (method === 'read_pos_data_uuid') {
            order.lines[0].refunded_orderline_id = { id: 9 };
        }
    } };
    assert.equal(await store.pay(), 'payment');
    assert.deepEqual(calls, ['read_refund_origin_for_payment', 'read_pos_data_uuid']);
});

test('creates quotation using one atomic RPC before removing the ticket', async () => {
    const { button, calls } = fixture();
    await button.onClickCreateSaleQuotation();
    assert.equal(calls[0], 'prices');
    assert.equal(calls[1].method, 'create_sale_quotation_from_ui');
    assert.equal(calls[1].args[0].state, 'draft');
    assert.equal(calls[1].args[1].lines[0][3], calls[1].args[0].price);
    assert.equal(calls[2], 'removePending');
    assert.equal(calls[3], 'remove');
});

test('sends zero for an unset component discount', async () => {
    const { button, calls } = fixture();
    const order = button.pos.getOrder();
    order.lines.push({ uuid: 'colorant', product_id: { id: 3 }, qty: 1, price_unit: 0,
        combo_parent_id: order.lines[0] });
    await button.onClickCreateSaleQuotation();
    assert.equal(calls[1].args[1].lines[1][4], 0);
    assert.equal(calls[1].args[1].lines[1][5], 'line');
});

test('keeps ticket on RPC failure and releases UI for retry', async () => {
    const { button, calls } = fixture(true);
    await button.onClickCreateSaleQuotation();
    assert.equal(calls.includes('remove'), false);
    assert.equal(calls.at(-1), 'unblock');
    assert.equal(button._creatingSaleQuotation, false);
});
