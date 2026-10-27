import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const root = new URL('../static/src/app/', import.meta.url);
const utility = fs.readFileSync(new URL('utils/tint_order.js', root), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
const helpers = new Function(utility + '\nreturn { isTintColorant, isTintedBase, tintParent, tintDose };')();
// Bind only the helpers actually imported by the production module. Injecting
// every helper globally hid missing imports such as tintParent after merging.
const source = (name) => fs.readFileSync(new URL('overrides/' + name, root), 'utf8')
    .replace(/^import \{ ([^}]+) \} from "@entintados_pdv\/app\/utils\/tint_order";\r?\n/gm,
        'const { $1 } = tintHelpers;\n')
    .replace(/^import .*;\r?\n/gm, '');
function patch(target, extension) {
    Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
}

function fixture() {
    class Ticket {
        onClickOrderline(line) { this.selected = line; }
        getSelectedOrder() { return this.order; }
        getSelectedOrderlineId() { return this.selected?.id; }
        getToRefundDetail(line) {
            return this.details[line.id] ||= { line, qty: 0 };
        }
        _doesOrderHaveSoleItem(order) { return order.lines.length === 1; }
        _prepareAutoRefundOnOrder() { this.nativeAuto = true; return true; }
        _onUpdateSelectedOrderline({ buffer }) {
            const parent = this.selected.combo_parent_id || this.selected;
            const detail = this.getToRefundDetail(parent);
            const qty = Math.abs(Number(buffer || 0));
            if (qty <= parent.qty - parent.refundedQty) detail.qty = qty;
            for (const child of parent.combo_line_ids) {
                this.getToRefundDetail(child).qty = child.qty / parent.qty * detail.qty;
            }
        }
        async addAdditionalRefundInfo() { this.nativeAdditional = true; }
    }
    class Line {
        setQuantity(value) {
            if (this.refunded_orderline_id && Number(value) > 0) return { title: 'Positive refund' };
            const old = this.qty;
            this.qty = Number(value);
            if (this.refunded_orderline_id) this.refundDetail = -this.qty;
            for (const child of this.combo_line_ids || []) child.setQuantity(child.qty / old * this.qty, true);
            return true;
        }
        setUnitPrice(value) { this.price_unit = value; }
        setDiscount(value) { this.discount = value; }
        canBeMergedWith() { return true; }
    }
    class Order {}
    const alerts = [];
    const context = { TicketScreen: Ticket, PosOrderline: Line, PosOrder: Order, patch, tintHelpers: helpers,
        _t: (s) => s, AlertDialog: class {}, console };
    vm.runInNewContext(source('pos_order_line.js'), context);
    const parent = Object.assign(new Line(), { id: 1, qty: 2, refundedQty: 0, price_unit: 500,
        product_id: { product_tmpl_id: { tint_role: 'base' } } });
    const children = [2.5, 0.25].map((dose, index) => Object.assign(new Line(), {
        id: index + 2, qty: dose * 2, refundedQty: 0, price_unit: 0,
        product_id: { product_tmpl_id: { tint_role: 'colorant' } }, combo_parent_id: parent, combo_line_ids: [],
    }));
    parent.combo_line_ids = children;
    const order = { lines: [parent, ...children], finalized: true, getOrderlines() { return this.lines; } };
    for (const line of order.lines) line.order_id = order;
    const screen = Object.assign(new Ticket(), { order, selected: parent, details: {},
        pos: { isProductQtyZero: (n) => Math.abs(n) < 1e-8 },
        numberBuffer: { reset() {} }, dialog: { add(...args) { alerts.push(args); } } });
    return { parent, children, order, screen, Line, Order, context, alerts };
}

test('editing the base preserves doses and protects standalone colorants', () => {
    const { parent, children } = fixture();
    assert.equal(children[0].setQuantity(100), false);
    assert.equal(parent.setQuantity(1), true);
    assert.equal(children[0].qty, 2.5);
    assert.equal(children[1].qty, 0.25);
    assert.equal(parent.setQuantity(0.5), false);
    assert.equal(children[0].qty, 2.5);
    assert.equal(parent.setQuantity(2), true);
    assert.equal(children[0].qty, 5);
});

test('refund edits update native component refund tracking and reject positive refunds', () => {
    const { parent, children } = fixture();
    parent.refunded_orderline_id = { qty: 2 };
    for (const child of children) child.refunded_orderline_id = { qty: child.qty, combo_parent_id: { qty: 2 } };
    parent.qty = -2;
    for (const child of children) child.qty *= -1;
    assert.equal(parent.setQuantity(-1), true);
    assert.equal(children[0].qty, -2.5);
    assert.equal(children[0].refundDetail, 2.5);
    assert.ok(parent.setQuantity(1).title);
    assert.equal(children[0].qty, -2.5);
});

test('refund prices and discounts are preserved when partner agreements change', () => {
    const { Order, context } = fixture();
    vm.runInNewContext(source('pos_store.js'), { ...context, PosStore: class {},
        getPartnerPricingRule: () => ({ type: 'fixed_price', price: 999 }) });
    const refund = { refunded_orderline_id: {}, price_unit: 500, discount: 12, product_id: {} };
    Object.assign(new Order(), { lines: [refund] })._entintadosReconcileDiscounts();
    assert.equal(refund.price_unit, 500);
    assert.equal(refund.discount, 12);
});

test('ordinary products continue through native selection', () => {
    const { screen } = fixture();
    const ordinary = { id: 80, product_id: {} };
    screen.onClickOrderline(ordinary);
    assert.equal(screen.selected, ordinary);
    assert.equal(Object.keys(screen.details).length, 0);
});

test('POS refund button and direct refund action are blocked', async () => {
    const { screen, alerts } = fixture();
    assert.equal(await screen.onClickRefund(), false);
    assert.equal(await screen.onDoRefund(), false);
    assert.equal(alerts.length, 2);
    assert.match(alerts[0][1].body, /bloqueada/);
    assert.equal(Object.keys(screen.details).length, 0);
});

test('product-screen refund control is blocked', () => {
    const notices = [];
    class ControlButtons {}
    vm.runInNewContext(source('control_buttons/tint_control_button.js'), {
        patch, ControlButtons, _t: (s) => s,
    });
    const control = Object.assign(new ControlButtons(), { notification: { add: (text) => notices.push(text) } });
    assert.equal(control.clickRefund(), false);
    assert.match(notices[0], /bloqueada/);
});