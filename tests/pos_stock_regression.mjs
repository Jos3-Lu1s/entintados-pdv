import fs from 'node:fs';
import assert from 'node:assert/strict';

const root = new URL('../', import.meta.url);
const utility = fs.readFileSync(new URL('static/src/app/utils/pos_stock.js', root), 'utf8');
const { stockError, requiresPosStock } = await import(
    'data:text/javascript;base64,' + Buffer.from(utility).toString('base64'));
const product = { id: 1, type: 'consu', is_storable: true, pos_stock_qty: 2,
    uom_id: { rounding: 1 }, name: 'Producto' };
assert.equal(requiresPosStock({ ...product, type: 'service' }), false);
assert.equal(requiresPosStock({ ...product, is_storable: false }), false);
assert.ok(stockError({ lines: [] }, { ...product, pos_stock_qty: 0 }, 1));
assert.equal(stockError({ lines: [] }, product, -1), null);

// Simula la fusión: el core mantiene ambas líneas en el ticket mientras suma.
const first = { product_id: product, qty: 1 };
const incoming = { product_id: product, qty: 1 };
const order = { lines: [first, incoming] };
first._stockMergeSource = incoming;
assert.equal(stockError(order, product, 2, first), null);
assert.ok(stockError(order, product, 3, first));

function patch(target, extension) {
    Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target),
        Object.getOwnPropertyDescriptors(target)));
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
}
class Mutex {
    tail = Promise.resolve();
    exec(fn) {
        const result = this.tail.then(fn);
        this.tail = result.catch(() => {});
        return result;
    }
}
class PosStore {
    async addLineToOrder(vals, order) {
        order.lines.push({ product_id: vals.product_id, qty: vals.qty ?? 1 });
        return order.lines.at(-1);
    }
}
class PosOrderline {
    setQuantity(qty) { this.qty = qty; return true; }
    merge(line) { return this.setQuantity(this.qty + line.qty); }
}
class OrderPaymentValidation {}
class AlertDialog {}
const source = fs.readFileSync(new URL('static/src/app/overrides/pos_stock.js', root), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
new Function('patch', 'PosStore', 'PosOrderline', 'AlertDialog', 'stockError',
    'requiresPosStock', 'Mutex', 'OrderPaymentValidation', source)(patch, PosStore,
    PosOrderline, AlertDialog, stockError, requiresPosStock, Mutex, OrderPaymentValidation);

const warnings = [];
const store = new PosStore();
store.notification = { add: (message) => warnings.push(message) };
store.dialog = { add() { throw new Error('Unexpected dialog'); } };
store.refreshProductStock = async () => {};
const empty = { ...product, pos_stock_qty: 0 };
const ticket = { lines: [] };
await Promise.all([store.addLineToOrder({ product_id: empty }, ticket),
    store.addLineToOrder({ product_id: empty }, ticket)]);
assert.equal(ticket.lines.length, 0, 'Dos clics sin stock no crean líneas');
assert.equal(warnings.length, 2);
await Promise.all([store.addLineToOrder({ product_id: product }, ticket),
    store.addLineToOrder({ product_id: product }, ticket),
    store.addLineToOrder({ product_id: product }, ticket)]);
assert.equal(ticket.lines.length, 2, 'Los clics simultáneos respetan las existencias');
const line = Object.assign(new PosOrderline(), { product_id: product, qty: 1,
    order_id: { lines: [] } });
line.order_id.lines.push(line);
assert.equal(line.setQuantity(3), false, 'No falla aunque models.env no exista');
assert.equal(line.qty, 1);
const mergeLine = { product_id: product, qty: 1 };
line.order_id.lines.push(mergeLine);
assert.equal(line.merge(mergeLine), true);
assert.equal(line.qty, 2, 'La fusión cuenta la línea entrante una sola vez');
console.log('Regresiones de stock: correctas');
