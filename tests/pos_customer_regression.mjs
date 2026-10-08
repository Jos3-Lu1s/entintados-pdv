import fs from 'node:fs';
import assert from 'node:assert/strict';

function patch(target, extension) {
    Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target),
        Object.getOwnPropertyDescriptors(target)));
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
}
class PosStore {
    async addLineToOrder() { this.added = (this.added || 0) + 1; return 'line'; }
    async pay() { this.paid = true; return true; }
    getOrder() { return this.order; }
}
class OrderPaymentValidation {
    async validateOrder() { this.validated = true; return true; }
}
const source = fs.readFileSync(new URL('../static/src/app/overrides/pos_customer.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
new Function('patch', '_t', 'PosStore', 'OrderPaymentValidation', source)(
    patch, (text) => text, PosStore, OrderPaymentValidation);
const pos = new PosStore();
pos.order = { partner_id: false };
pos.notification = { add() {} };
const validation = Object.assign(new OrderPaymentValidation(), { pos, order: pos.order });
await pos.addLineToOrder({}, pos.order);
await pos.pay();
assert.equal(await validation.validateOrder(), false);
assert.equal(pos.added, undefined);
assert.equal(pos.paid, undefined);
assert.equal(validation.validated, undefined);
pos.order.partner_id = { id: 7 };
assert.equal(await pos.addLineToOrder({}, pos.order), 'line');
assert.equal(await pos.pay(), true);
assert.equal(await validation.validateOrder(), true);
pos.order.partner_id = false;
await pos.addLineToOrder({}, pos.order);
assert.equal(pos.added, 1, 'Quitar el cliente vuelve a bloquear los productos');
const anotherOrder = { partner_id: false };
await pos.addLineToOrder({}, anotherOrder);
assert.equal(pos.added, 1, 'Se valida el cliente del ticket de destino');
console.log('Cliente obligatorio: regresiones correctas');
