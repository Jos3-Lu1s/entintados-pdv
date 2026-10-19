import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/overrides/pos_customer.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

test('A finalized purchase refreshes customer funds; failed synchronization does not', async () => {
    class Store {}
    class Validation { async afterOrderValidation() {} }
    vm.runInNewContext(source, { PosStore: Store, OrderPaymentValidation: Validation, _t: (s) => s,
        patch(target, extension) {
            Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        } });
    const partner = { id: 8 };
    const validation = new Validation();
    let refreshed = 0;
    validation.pos = { refreshTotalDueOfPartner: async (p) => { assert.equal(p, partner); refreshed++; } };
    validation.order = { finalized: false, getPartner: () => partner, lines: [{ advance_source_ref: 42 }] };
    await validation.afterOrderValidation();
    assert.equal(refreshed, 0);
    validation.order.finalized = true;
    await validation.afterOrderValidation();
    assert.equal(refreshed, 1);
});
