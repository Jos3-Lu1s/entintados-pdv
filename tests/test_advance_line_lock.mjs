import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/overrides/advance_line_lock.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture(advance = true) {
    class Line {
        setQuantity(value) { this.qty = Number(value); return true; }
        setUnitPrice(value) { this.price_unit = Number(value); }
        setDiscount(value) { this.discount = Number(value); }
        setNote(value) { this.note = value; }
        setCustomerNote(value) { this.customer_note = value; }
        canBeMergedWith() { return true; }
    }
    class Order {
        removeOrderline(line) {
            this.removed = line;
            this.lines = this.lines.filter((item) => item !== line);
            return true;
        }
    }
    class Summary {
        updateSelectedOrderline() { this.updated = true; }
        _setValue() { this.updated = true; }
        onOrderlineLongPress() { this.edited = true; }
        setLinePrice() { this.edited = true; }
        editPackLotLines() { this.edited = true; }
    }
    class Screen { getNumpadButtons() { return [{ value: 'quantity' }, { value: 'price' }, { value: 'discount' }, { value: 'Backspace' }]; } }
    vm.runInNewContext(source, {
        PosOrderline: Line, PosOrder: Order, OrderSummary: Summary, ProductScreen: Screen,
        _t: (text) => text,
        patch(target, extension) {
            Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        },
    });
    const line = Object.assign(new Line(), {
        config: { down_payment_product_id: { id: 5 } },
        product_id: { id: advance ? 5 : 9, product_tmpl_id: {} },
        qty: 1, price_unit: 250, discount: 0,
    });
    const order = new Order();
    order.lines = [line];
    order.uiState = {};
    order.getSelectedOrderline = () => line;
    const summary = Object.assign(new Summary(), {
        currentOrder: order, pos: { notification: { add() {} } },
        numberBuffer: { reset() {} },
    });
    const screen = Object.assign(new Screen(), { currentOrder: order });
    return { line, order, summary, screen };
}

test('Configured advance rejects quantity, price, discount and note changes', () => {
    const { line } = fixture();
    line.setQuantity(2);
    line.setQuantity(-1);
    line.setUnitPrice(1);
    line.setDiscount(50);
    line.setNote('Changed');
    line.setCustomerNote('Changed');
    assert.equal(line.qty, 1);
    assert.equal(line.price_unit, 250);
    assert.equal(line.discount, 0);
    assert.equal(line.note, undefined);
    assert.equal(line.customer_note, undefined);
});

test('Native initialization can reapply unchanged values', () => {
    const { line } = fixture();
    assert.equal(line.setQuantity(1), true);
    line.setUnitPrice(250);
    assert.equal(line.price_unit, 250);
});

test('Advance applications and legacy generic products are protected', () => {
    const { line } = fixture(false);
    line.advance_source_ref = 12;
    assert.equal(line.isAdvanceLocked, true);
    line.advance_source_ref = 0;
    line.product_id.product_tmpl_id.is_accounting_advance = true;
    assert.equal(line.isAdvanceLocked, true);
});

test('Ticket allows deleting advances but blocks all edits and long press', async () => {
    const { line, order, summary, screen } = fixture();
    await summary.updateSelectedOrderline({ buffer: '5', key: '5' });
    summary._setValue('remove');
    await summary.onOrderlineLongPress({}, line);
    await summary.setLinePrice(line, 1);
    await summary.editPackLotLines(line);
    order.removeOrderline(line);
    assert.equal(summary.updated, undefined);
    assert.equal(summary.edited, undefined);
    assert.equal(order.removed, line);
    assert.ok(screen.getNumpadButtons().every((button) => button.disabled === (button.value !== 'Backspace')));
});

test('Backspace removes the entire advance without changing its quantity or price', async () => {
    const { line, order, summary } = fixture();
    await summary.updateSelectedOrderline({ buffer: '25', key: 'Backspace' });
    assert.equal(order.removed, line);
    assert.equal(line.qty, 1);
    assert.equal(line.price_unit, 250);
    assert.equal(summary.updated, undefined);
});

test('Advance lines never merge into a different amount', () => {
    const advance = fixture().line;
    const ordinary = fixture(false).line;
    assert.equal(advance.canBeMergedWith(ordinary), false);
    assert.equal(ordinary.canBeMergedWith(advance), false);
});

test('Ordinary products retain all native editing and deletion', async () => {
    const { line, order, summary, screen } = fixture(false);
    line.setQuantity(3);
    line.setUnitPrice(100);
    line.setDiscount(10);
    line.setNote('OK');
    line.setCustomerNote('OK');
    await summary.updateSelectedOrderline({ buffer: '3', key: '3' });
    await summary.onOrderlineLongPress({}, line);
    order.removeOrderline(line);
    assert.equal(line.qty, 3);
    assert.equal(line.price_unit, 100);
    assert.equal(line.discount, 10);
    assert.equal(line.note, 'OK');
    assert.equal(line.customer_note, 'OK');
    assert.equal(summary.updated, true);
    assert.equal(summary.edited, true);
    assert.equal(order.removed, line);
    assert.ok(screen.getNumpadButtons().every((button) => !button.disabled));
});
