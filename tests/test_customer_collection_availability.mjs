import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../static/src/app/overrides/pos_financing.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
function fixture(hasInvoices = false) {
    class Controls { setup() {} }
    vm.runInNewContext(source, {
        PaymentScreen: class {}, ControlButtons: Controls, CustomSelectCreateDialog: class {},
        useState: (value) => value, useEffect() {}, _t: (value) => value,
        patch(target, extension) {
            Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        },
    });
    const controls = new Controls();
    const order = { uuid: 'test', partner: { id: 1, is_credit: true }, lines: [], payment_ids: [],
        getPartner() { return this.partner; } };
    controls.pos = { getOrder: () => order, config: { id: 1 },
        data: { call: async () => ({ has_outstanding_invoices: hasInvoices }) } };
    controls.notification = { add() {} };
    controls.dialog = { add() { controls.opened = true; } };
    controls.setup();
    return { controls, order };
}
test('collection remains disabled until server confirms outstanding invoices', async () => {
    const { controls } = fixture(false);
    assert.equal(controls.canCollectOutstandingInvoice, false);
    await controls.refreshCustomerFunds();
    assert.equal(controls.canCollectOutstandingInvoice, false);
    await controls.onClickCollectOutstandingInvoice();
    assert.equal(controls.opened, undefined);
});
test('an actual pending invoice enables collection independently of granting new credit', async () => {
    const { controls, order } = fixture(true);
    order.partner.is_credit = false;
    await controls.refreshCustomerFunds();
    assert.equal(controls.canCollectOutstandingInvoice, true);
    order.lines.push({});
    assert.equal(controls.canCollectOutstandingInvoice, false);
});
test('old customer response cannot enable collection for a different customer', async () => {
    const { controls, order } = fixture(true);
    let reply;
    controls.pos.data.call = () => new Promise((resolve) => { reply = resolve; });
    const pending = controls.refreshCustomerFunds();
    order.partner = { id: 2 };
    reply({ has_outstanding_invoices: true });
    await pending;
    assert.equal(controls.canCollectOutstandingInvoice, false);
});
test('clearing the customer disables collection', async () => {
    const { controls, order } = fixture(true);
    await controls.refreshCustomerFunds();
    order.partner = false;
    await controls.refreshCustomerFunds();
    assert.equal(controls.canCollectOutstandingInvoice, false);
});
