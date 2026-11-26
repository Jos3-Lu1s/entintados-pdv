import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/overrides/pos_sale_quotation.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
const quotationUtils = fs.readFileSync(new URL('../static/src/app/utils/quotation_print.js', import.meta.url), 'utf8')
    .replace(/^export /gm, '');

function fixture(fail = false) {
    class ControlButtons {}
    class TicketScreen {}
    class NativePosStore {
        async _processSaleOrder(saleOrder) {
            const order = this.getOrder();
            order.lines.push({ sale_order_origin_id: saleOrder });
            return order;
        }
        pay() {
            assert.ok(this.getOrder().lines.some((line) => line.refunded_orderline_id));
            return 'payment';
        }
    }
    class PosStore extends NativePosStore {}
    const BranchStockDialog = class {};
    vm.runInNewContext(quotationUtils + source, { ControlButtons, TicketScreen, PosStore, PosOrder: class {}, PaymentScreen: class {}, useService() {}, ask: async () => true,
        BranchStockDialog, FormViewDialog: class {}, SelectCreateDialog: class {},
        requiresPosStock: (product) => Boolean(product?.is_storable),
        downloadFile: (...args) => downloads.push(args),
        isTintColorant: (line) => Boolean(line.is_tint_colorant),
        _t: (text) => text, patch: (target, extension) => {
            Object.setPrototypeOf(extension, Object.getPrototypeOf(target));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        } });
    const calls = [];
    const downloads = [];
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
    return { button, calls, PosStore, TicketScreen, BranchStockDialog, downloads };
}

test('filters orders marked To Invoice and invoiced quotations from the import selector', async () => {
    const { button } = fixture();
    let options;
    let selected;
    button.dialog.add = (_dialog, props) => { options = props; };
    button.pos.currency = { id: 5 };
    button.pos.onClickSaleOrder = (id) => { selected = id; };

    button.onClickQuotation();
    assert.equal(options.resModel, 'sale.order');
    assert.equal(JSON.stringify(options.domain), JSON.stringify([
        ['state', '!=', 'cancel'],
        ['invoice_status', '!=', 'to invoice'],
        ['pos_has_active_invoice', '=', false],
        ['currency_id', '=', 5],
        ['amount_unpaid', '>', 0],
        ['partner_id', 'any', [['id', 'child_of', [1]]]],
    ]));
    await options.onSelected([42]);
    assert.equal(selected, 42);
});

test('downloads the linked quotation from the clicked ticket row', async () => {
    const { TicketScreen } = fixture();
    const screen = new TicketScreen();
    const calls = [];
    screen.quotationAction = { doAction: (...args) => calls.push(args) };
    screen.pos = { lastCreatedSaleQuotation: { id: 99 }, getOrder: () => ({
        lines: [{ sale_order_origin_id: { id: 42 } }],
    }) };
    const row = { lines: [{ sale_order_line_id: { order_id: { id: 43 } } }] };
    const unlinked = { lines: [] };

    assert.equal(screen.hasLinkedQuotation(unlinked), false);
    await screen.onClickDownloadQuotation(unlinked);
    assert.equal(screen.hasLinkedQuotation(row), true);
    await screen.onClickDownloadQuotation(row);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], 'sale.action_report_saleorder');
    assert.equal(calls[0][1].additionalContext.active_id, 43);
});

test('opens a different sales quotation in a new POS order and warns the cashier', async () => {
    const { PosStore } = fixture();
    const firstQuotation = { id: 42, partner_id: { id: 1 } };
    const secondQuotation = { ...firstQuotation, id: 43 };
    const original = { lines: [{ sale_order_line_id: { order_id: firstQuotation } }] };
    let current = original;
    const notices = [];
    const store = Object.assign(new PosStore(), {
        getOrder: () => current,
        addNewOrder() { current = { lines: [] }; },
        notification: { add: (...args) => notices.push(args) },
    });

    assert.equal(await store._processSaleOrder(firstQuotation), original);
    assert.equal(notices.length, 0);
    const next = await store._processSaleOrder(secondQuotation);
    assert.equal(next, current);
    assert.notEqual(next, original);
    assert.equal(next.lines.length, 1);
    assert.equal(next.lines[0].sale_order_origin_id, secondQuotation);
    assert.equal(original.lines.length, 2);
    assert.equal(original.lines[1].sale_order_origin_id, firstQuotation);
    assert.equal(notices.length, 1);
    assert.equal(notices[0][1].type, 'warning');
    assert.match(notices[0][0], /se abrirá en una orden nueva/);
});

test('prints all current order lines without converting or removing the ticket', async () => {
    const { button, calls, downloads } = fixture();
    const order = button.pos.getOrder();
    const base = order.lines[0];
    base.serializeForORM = () => ({ uuid: base.uuid, product_id: 2, price_subtotal: base.price_subtotal });
    order.lines.push({
        combo_parent_id: base,
        serializeForORM: () => ({ uuid: 'component', product_id: 3 }),
    });
    order.amount_total = 0;
    order.tracking_number = '6009';
    order.setOrderPrices = () => {
        calls.push('prices');
        order.amount_total = 65076.70;
        order.amount_tax = 8976.10;
        base.price_subtotal = 56100.60;
    };
    order.serializeForORM = () => ({ state: 'draft', lines: [], amount_total: order.amount_total,
        amount_tax: order.amount_tax });
    button.pos.data.call = async (model, method, [snapshot]) => {
        assert.equal(model, 'pos.order');
        assert.equal(method, 'print_sale_quotation_from_ui');
        assert.equal(snapshot.amount_total, 65076.70);
        assert.equal(snapshot.tracking_number, '6009');
        assert.equal(snapshot.amount_tax, 8976.10);
        assert.equal(JSON.stringify(snapshot.lines), '[[0,0,{"uuid":"line","product_id":2,"price_subtotal":56100.6,"combo_parent_id":false}],[0,0,{"uuid":"component","product_id":3,"combo_parent_id":"line"}]]');
        return 'JVBERi0=';
    };
    await button.onClickPrintCurrentQuotation();
    assert.deepEqual(downloads, [['data:application/pdf;base64,JVBERi0=', 'Cotizacion.pdf', 'application/pdf']]);
    assert.deepEqual(calls, ['prices', 'unblock']);
    assert.equal(button.pos.getOrder(), order);
});

test('opens the original invoice when SAT confirmation is pending', async () => {
    const { button } = fixture();
    button.pos.session = { id: 7 };
    button.pos.data.call = async (model, method, args) => {
        assert.equal(method, 'restore_order');
        assert.equal(JSON.stringify(args), '["pos.order",8,7,true]');
        return { invoice_id: 9 };
    };
    let opened;
    button.dialog.add = (dialog, props) => { opened = props; };
    await button.recoverReinvoiceTicket('pos.order', 8);
    assert.equal(opened.resModel, 'account.move');
    assert.equal(opened.resId, 9);
});

test('selects the new draft ticket after restoring an order from Ventas', async () => {
    const { button } = fixture();
    button.pos.session = { id: 7 };
    button.pos.data.call = async () => ({ uuid: 'replacement' });
    const replacement = { uuid: 'replacement', state: 'draft' };
    button.pos.data.loadServerOrders = async () => [replacement];
    let selected;
    button.pos.setOrder = (ticket) => { selected = ticket; };
    await button.recoverReinvoiceTicket('sale.order', 8);
    assert.equal(selected, replacement);
});

test('checks branch availability for positive stock lines without saving the quotation', async () => {
    const { button, calls, BranchStockDialog } = fixture();
    const order = button.pos.getOrder();
    const base = { id: 2, is_storable: true };
    const nativeColorant = { product_id: { id: 3 }, qty: 24, is_tint_colorant: true };
    const importedColorant = { product_id: { id: 3 }, qty: 4, is_tint_colorant: true };
    order.lines = [
        { product_id: base, qty: 1 },
        { product_id: base, qty: 2, combo_line_ids: [nativeColorant] },
        { product_id: base, qty: -1 },
        { product_id: base, qty: 2, combo_line_ids: [importedColorant], sale_order_line_id: {
            tint_snapshot: { base_id: 2, doses: [{ product_id: 3, points: 2 }, { product_id: 5, points: 8 }] },
        } },
        nativeColorant,
        importedColorant,
        { product_id: { id: 4 }, qty: 1 },
    ];
    const stockRows = [{ warehouse: 'Centro', product: 'Base', available: 2, shortage: true }];
    button.pos.config = { id: 7 };
    button.pos.data.call = async (model, method, args) => {
        calls.push([model, method, JSON.stringify(args)]);
        return stockRows;
    };
    button.dialog.add = (component, props) => {
        assert.equal(component, BranchStockDialog);
        assert.equal(props.rows, stockRows);
        calls.push('opened');
    };
    await button.onClickQuotationStock();
    assert.deepEqual(calls, [
        ['product.product', 'get_pos_quotation_branch_stock', '[7,{"2":5},{"3":28,"5":16}]'],
        'opened',
    ]);
    order.finalized = true;
    assert.equal(button.canCheckQuotationStock, false);
});

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
