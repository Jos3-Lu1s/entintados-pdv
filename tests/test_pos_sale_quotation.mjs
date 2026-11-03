import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/overrides/pos_sale_quotation.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture(fail = false) {
    class ControlButtons {}
    class PosStore {}
    vm.runInNewContext(source, { ControlButtons, PosStore, useService() {}, ask: async () => true,
        _t: (text) => text, patch: (target, extension) => Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension)) });
    const calls = [];
    const order = { id: 'local', uuid: 'ticket', state: 'draft', payment_ids: [],
        getPartner: () => ({ id: 1 }), lines: [{ uuid: 'line', product_id: { id: 2 }, qty: 1, price_unit: 100, discount: 0 }],
        setOrderPrices() { calls.push('prices'); }, serializeForORM() { return { uuid: this.uuid, state: this.state }; } };
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
    return { button, calls };
}

test('creates quotation using one atomic RPC before removing the ticket', async () => {
    const { button, calls } = fixture();
    await button.onClickCreateSaleQuotation();
    assert.equal(calls[0], 'prices');
    assert.equal(calls[1].method, 'create_sale_quotation_from_ui');
    assert.equal(calls[1].args[0].state, 'draft');
    assert.equal(calls[2], 'removePending');
    assert.equal(calls[3], 'remove');
});

test('keeps ticket on RPC failure and releases UI for retry', async () => {
    const { button, calls } = fixture(true);
    await button.onClickCreateSaleQuotation();
    assert.equal(calls.includes('remove'), false);
    assert.equal(calls.at(-1), 'unblock');
    assert.equal(button._creatingSaleQuotation, false);
});
