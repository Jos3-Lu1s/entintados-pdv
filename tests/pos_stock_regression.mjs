import fs from 'node:fs';
import assert from 'node:assert/strict';

const root = new URL('../', import.meta.url);
const utility = fs.readFileSync(new URL('static/src/app/utils/pos_stock.js', root), 'utf8');
const { stockError, requiresPosStock } = await import(
    'data:text/javascript;base64,' + Buffer.from(utility).toString('base64'));
const product = { id: 1, type: 'consu', is_storable: true, pos_stock_qty: 0,
    uom_id: { rounding: 1 }, name: 'Producto' };
assert.equal(requiresPosStock({ ...product, type: 'service' }), false);
assert.equal(requiresPosStock({ ...product, is_storable: false }), false);
assert.ok(stockError({ lines: [] }, product, 0.01));
assert.ok(stockError({ lines: [] }, { ...product, pos_stock_qty: -0.01 }, 0.01));
assert.equal(stockError({ lines: [] }, product, -1), null);

function patch(target, extension) {
    Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target),
        Object.getOwnPropertyDescriptors(target)));
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
}
class PosStore {
    async addLineToOrder(vals, order) {
        order.lines.push({ product_id: vals.product_id, qty: vals.qty ?? 1 });
        return order.lines.at(-1);
    }
    getOrder() { return this.order; }
    async pay() { return 'payment screen'; }
}
class PaymentScreen {
    async addNewPaymentLine() { return 'payment line'; }
    async sendPaymentRequest() { return 'terminal request'; }
}
class OrderPaymentValidation {
    async isOrderValid() { return true; }
}
class AlertDialog {}
const source = fs.readFileSync(new URL('static/src/app/overrides/pos_stock.js', root), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
new Function('patch', '_t', 'PosStore', 'PaymentScreen', 'AlertDialog', 'stockError',
    'requiresPosStock', 'OrderPaymentValidation', source)(patch, (message) => message,
    PosStore, PaymentScreen, AlertDialog, stockError, requiresPosStock, OrderPaymentValidation);

const warnings = [];
const store = new PosStore();
store.notification = { add: (message) => warnings.push(message) };
store.dialog = { add: (_component, props) => warnings.push(props.body) };
store.refreshProductStock = async () => {};
const ticket = { lines: [] };
store.order = ticket;
const screen = Object.assign(new PaymentScreen(), { pos: store, currentOrder: ticket });
const validation = Object.assign(new OrderPaymentValidation(), { pos: store, order: ticket });
await store.addLineToOrder({ product_id: product }, ticket);
await store.addLineToOrder({ product_id: product }, ticket);
assert.equal(ticket.lines.length, 2, 'Se pueden agregar productos sin stock');
assert.equal(await store.pay(), false);
assert.equal(await screen.addNewPaymentLine(), false);
assert.equal(await screen.sendPaymentRequest(), false);
assert.equal(await validation.isOrderValid(), false);
assert.match(warnings.at(-1), /solicitadas en el ticket: 2/);
product.pos_stock_qty = 2;
assert.equal(await store.pay(), 'payment screen');
assert.equal(await screen.addNewPaymentLine(), 'payment line');
assert.equal(await screen.sendPaymentRequest(), 'terminal request');
assert.equal(await validation.isOrderValid(), true);
store.refreshProductStock = async () => { throw new Error('offline'); };
assert.equal(await screen.addNewPaymentLine(), false);
ticket.lines[0].qty = -1;
ticket.lines[1].qty = -1;
assert.equal(await validation.isOrderValid(), true, 'La devolución no requiere existencias');
await store.addLineToOrder({ product_id: { ...product, product_tmpl_id: { tint_role: 'colorant' } } }, ticket);
assert.equal(ticket.lines.length, 2, 'Los colorantes individuales siguen bloqueados');
console.log('Regresiones de stock: correctas');
