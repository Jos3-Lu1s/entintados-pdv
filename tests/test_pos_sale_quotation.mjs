import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const readOverride = (name) => fs.readFileSync(new URL(`../static/src/app/overrides/${name}`, import.meta.url), 'utf8')
    .replace(/^import [\s\S]*?;\r?\n/gm, '');
const source = readOverride('pos_sale_quotation.js') + readOverride('sale_order_import.js');
const readUtil = (name) => fs.readFileSync(new URL(`../static/src/app/utils/${name}`, import.meta.url), 'utf8')
    .replace(/^export /gm, '');
const quotationUtils = readUtil('quotation_print.js') + readUtil('sale_order_import.js');

function fixture(fail = false) {
    class ControlButtons {}
    class TicketScreen {}
    class NativePosStore {
        async _processSaleOrder() {
            assert.fail('The native action menu must not open');
        }
        pay() {
            assert.ok(this.getOrder().lines.some((line) => line.refunded_orderline_id));
            return 'payment';
        }
        async settleSO(saleOrder) {
            this.getOrder().setPricelist(saleOrder.pricelist_id);
        }
        _refreshLinePromotions(order) {
            (this.promotionRefreshes ||= []).push(order);
        }
    }
    class PosStore extends NativePosStore {}
    class NativePosOrderline {
        setQuantityFromSOL(saleOrderLine) {
            this.qty = saleOrderLine.product_uom_qty;
        }
    }
    class PosOrderline extends NativePosOrderline {}
    const BranchStockDialog = class {};
    vm.runInNewContext(quotationUtils + source, { ControlButtons, TicketScreen, PosStore, PosOrderline, PosOrder: class {}, PaymentScreen: class {}, useService() {}, ask: async () => true,
        BranchStockDialog, FormViewDialog: class {}, SelectCreateDialog: class {},
        requiresPosStock: (product) => Boolean(product?.is_storable),
        downloadFile: (...args) => downloads.push(args),
        isTintColorant: (line) => Boolean(line.is_tint_colorant),
        isTintedBase: (line) => Boolean(line.is_tinted_base),
        computeTintedPriceDetails: (pos, base, formula, partner, doses) => ({
            finalPrice: base.lst_price + doses.reduce((sum, dose) => sum + dose.points * dose.colorant.lst_price, 0),
        }),
        _t: (text, ...values) => (values[0] && typeof values[0] === 'object'
            ? text.replace(/%\((\w+)\)s/g, (_match, key) => values[0][key])
            : values.reduce((result, value) => result.replace('%s', value), text)),
        patch: (target, extension) => {
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
    return { button, calls, PosStore, PosOrderline, TicketScreen, BranchStockDialog, downloads };
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

test('opens the sales quotation in a new ticket when the current one has lines', async () => {
    const { PosStore } = fixture();
    const firstQuotation = { id: 42, partner_id: { id: 1 }, fiscal_position_id: { id: 7 } };
    const secondQuotation = { ...firstQuotation, id: 43 };
    const newOrder = () => ({
        getName: () => '6009',
        lines: [],
        setPartner(partner) { this.partner_id = partner; },
        update(values) { Object.assign(this, values); },
    });
    const original = newOrder();
    let current = original;
    const notices = [];
    const store = Object.assign(new PosStore(), {
        getOrder: () => current,
        getOpenOrders: () => current === original ? [original] : [original, current],
        addNewOrder() { current = newOrder(); },
        async settleSO(quotation, fiscalPosition) {
            assert.equal(current.partner_id, quotation.partner_id);
            assert.equal(current.fiscal_position_id, fiscalPosition);
            current.lines.push({ sale_order_origin_id: quotation });
        },
        selectOrderLine(order, line) { assert.equal(line, order.lines.at(-1)); },
        notification: { add: (...args) => notices.push(args) },
    });

    await store._processSaleOrder(firstQuotation);
    assert.equal(current, original);
    assert.equal(notices.length, 0);
    assert.equal(await store._processSaleOrder(firstQuotation), undefined);
    assert.equal(current, original);
    assert.equal(original.lines.length, 1);
    assert.match(notices[0][0], /ya está en la orden 6009/);
    assert.equal(notices[0][1].type, 'warning');
    await store._processSaleOrder(secondQuotation);
    const next = current;
    assert.equal(next, current);
    assert.notEqual(next, original);
    assert.equal(next.lines.length, 1);
    assert.equal(next.lines[0].sale_order_origin_id, secondQuotation);
    assert.equal(original.lines.length, 1);
    assert.equal(original.lines[0].sale_order_origin_id, firstQuotation);
    assert.equal(notices[1][1].type, 'warning');
    assert.match(notices[1][0], /se abrirá en una orden nueva/);
    assert.equal(await store._processSaleOrder(firstQuotation), undefined);
    assert.equal(current, next);
    assert.equal(next.lines.length, 1);
    assert.match(notices[2][0], /ya está en la orden 6009/);

    const captured = newOrder();
    const product = { product_id: { id: 7 } };
    captured.lines.push(product);
    current = captured;
    const thirdQuotation = { ...firstQuotation, id: 44 };
    await store._processSaleOrder(thirdQuotation);
    assert.notEqual(current, captured);
    assert.deepEqual(captured.lines, [product]);
    assert.equal(current.lines[0].sale_order_origin_id, thirdQuotation);
    assert.match(notices[3][0], /se abrirá en una orden nueva/);
});

test('prints the original Sales quotation for an imported ticket', async () => {
    const { button, calls } = fixture();
    const order = button.pos.getOrder();
    const quotation = { id: 43, name: 'S00114' };
    order.lines[0].sale_order_origin_id = quotation;
    button.pos.lastCreatedSaleQuotation = { id: 99 };
    button.quotationAction = { doAction: (action, options) => {
        calls.push([action, options.additionalContext.active_id]);
    } };

    assert.equal(button.canPrintCurrentQuotation, false);
    await button.onClickPrintCurrentQuotation();
    assert.equal(calls.length, 0);
    await button.onClickPrintSaleQuotation();
    assert.deepEqual(calls, [['sale.action_report_saleorder', 43]]);
});

test('settles with the customer pricelist available in the POS, not the quotation one', async () => {
    const { PosStore } = fixture();
    const quotationPricelist = { id: 1 };
    const customerPricelist = { id: 2 };
    const posPricelist = { id: 3 };
    const order = { lines: [], partner: null, pricelist_id: null, refreshOrderPricing() {},
        getPartner() { return this.partner; }, setPricelist(pricelist) { this.pricelist_id = pricelist; } };
    const store = Object.assign(new PosStore(), {
        getOrder: () => order,
        config: { pricelist_id: posPricelist, use_presets: false },
        models: { 'product.pricelist': [quotationPricelist, customerPricelist, posPricelist] },
    });

    order.partner = { property_product_pricelist: { id: 2 } };
    await store.settleSO({ pricelist_id: quotationPricelist });
    assert.equal(order.pricelist_id, customerPricelist);

    order.partner = { property_product_pricelist: { id: 99 } };
    await store.settleSO({ pricelist_id: quotationPricelist });
    assert.equal(order.pricelist_id, posPricelist);
});

test('settling drops quotation prices and discounts before one final recalculation', async () => {
    const { PosStore } = fixture();
    const quoted = (id) => ({ sale_order_line_id: { id, is_downpayment: false }, price_type: 'automatic',
        price_unit: 90, discount: 10, pricing_rule_type: 'manual', pricing_rule_origin: 'Manual 10%',
        pricing_rule_id: false, promo_reward_id: { id: 7 } });
    const lines = [quoted(1), quoted(2), quoted(3)];
    const deduction = { sale_order_line_id: { id: 4, is_downpayment: true }, price_type: 'automatic',
        price_unit: -50, discount: 0 };
    const tinted = { ...quoted(5), is_tinted_base: true, product_id: { lst_price: 200 } };
    const captured = { product_id: { id: 9 }, price_type: 'manual', discount: 3 };
    const order = { lines: [...lines, deduction, tinted, captured], refreshed: [],
        getPartner: () => null, setPricelist() {}, _writeLinePrice(line, price) { line.price_unit = price; },
        refreshOrderPricing() { this.refreshed.push(this.lines.map((line) => line.discount)); } };
    const store = Object.assign(new PosStore(), {
        getOrder: () => order, config: { pricelist_id: { id: 3 }, use_presets: false },
        models: { 'product.pricelist': [] },
    });

    await store.settleSO({ pricelist_id: { id: 1 } });
    for (const line of lines) {
        assert.equal(line.price_type, 'original');
        assert.equal(line.discount, 0);
        assert.equal(line.pricing_rule_type, 'none');
        assert.equal(line.promo_reward_id, false);
    }
    assert.equal(deduction.price_type, 'automatic');
    assert.equal(tinted.discount, 0);
    assert.equal(tinted.price_type, 'manual');
    assert.equal(captured.discount, 3);
    assert.deepEqual(order.refreshed, [[0, 0, 0, 0, 0, 3]]);
});

test('lets the POS promotion engine decide after the final recalculation', async () => {
    const { PosStore } = fixture();
    const line = { sale_order_line_id: { id: 1, is_downpayment: false }, price_type: 'automatic',
        discount: 15, pricing_rule_type: 'promo', promo_reward_id: { id: 7 } };
    const steps = [];
    const order = { lines: [line], getPartner: () => null, setPricelist() {},
        refreshOrderPricing() { steps.push('pricing'); } };
    const store = Object.assign(new PosStore(), {
        getOrder: () => order, config: { pricelist_id: { id: 3 }, use_presets: false },
        models: { 'product.pricelist': [] },
        _refreshLinePromotions(target) { steps.push(['promotions', target, target.lines[0].promo_reward_id]); },
    });

    await store.settleSO({});
    assert.deepEqual(steps, ['pricing', ['promotions', order, false]]);
});

function tintFixture(snapshot, products) {
    const { PosStore } = fixture();
    const line = { sale_order_line_id: { id: 1, is_downpayment: false, tint_snapshot: snapshot },
        is_tinted_base: true, product_id: { lst_price: 200, display_name: 'Base W' }, price_type: 'automatic',
        price_unit: 224, discount: 10, pricing_rule_type: 'promo', promo_reward_id: { id: 7 } };
    const notices = [];
    const order = { lines: [line], getPartner: () => null, setPricelist() {},
        _writeLinePrice(target, price) { target.price_unit = price; },
        refreshOrderPricing() { line.price_origin = 'tint'; } };
    const byId = new Map(products.map((product) => [product.id, product]));
    const store = Object.assign(new PosStore(), {
        getOrder: () => order, config: { pricelist_id: { id: 3 }, use_presets: false },
        notification: { add: (...args) => notices.push(args) },
        models: { 'product.pricelist': [], 'product.product': { get: (id) => byId.get(id) } },
    });
    return { store, line, notices };
}

test('reprices a quoted tint with today base and colorant prices on the frozen points', async () => {
    const snapshot = { doses: [{ product_id: 21, points: 12, name: 'Colorante A', unit_price: 1 }] };
    const { store, line, notices } = tintFixture(snapshot, [{ id: 21, lst_price: 2 }]);

    await store.settleSO({});
    assert.equal(line.price_unit, 224);
    assert.equal(line.price_type, 'manual');
    assert.equal(line.price_origin, 'tint');
    assert.equal(line.discount, 0);
    assert.equal(line.promo_reward_id, false);
    assert.equal(notices.length, 0);
});

test('keeps the quoted tint price when a colorant is not loaded in the POS', async () => {
    const snapshot = { doses: [{ product_id: 21, points: 12, name: 'Colorante A' }] };
    const { store, line, notices } = tintFixture(snapshot, []);

    await store.settleSO({});
    assert.equal(line.price_unit, 224);
    assert.equal(line.price_type, 'manual');
    assert.equal(line.price_origin, 'manual');
    assert.equal(notices.length, 1);
    assert.match(notices[0][0], /Colorante A/);
});

function totalFixture(lineTotal) {
    const { PosStore, PosOrderline } = fixture();
    const quotation = { id: 42, name: 'S00042' };
    const line = Object.assign(new PosOrderline(), {
        sale_order_line_id: { id: 1, is_downpayment: false }, sale_order_origin_id: quotation,
        price_type: 'automatic', discount: 10, priceIncl: lineTotal,
    });
    // Ventas cotizó 4 cajas (8 piezas) por $464; la caja trae las 8 piezas.
    line.setQuantityFromSOL({ product_uom_qty: 8, price_total: 464 });
    const notices = [];
    const order = { lines: [line], getPartner: () => null, setPricelist() {}, refreshOrderPricing() {} };
    const store = Object.assign(new PosStore(), {
        getOrder: () => order, config: { pricelist_id: { id: 3 }, use_presets: false },
        models: { 'product.pricelist': [] },
        currency: { isZero: (value) => Math.abs(value) < 0.005 },
        env: { utils: { formatCurrency: (value) => `$ ${value.toFixed(2)}` } },
        notification: { add: (...args) => notices.push(args) },
    });
    return { store, quotation, line, notices };
}

test('warns with both totals when current rules change the quoted amount', async () => {
    const { store, quotation, line, notices } = totalFixture(406);

    await store.settleSO(quotation);
    assert.equal(line.qty, 8);
    assert.equal(notices.length, 1);
    assert.equal(notices[0][1].type, 'warning');
    assert.equal(notices[0][0],
        'La cotización S00042 valía $ 464.00; con las reglas vigentes el ticket queda en $ 406.00.');
});

test('does not warn when the ticket keeps the quoted amount', async () => {
    const { store, quotation, notices } = totalFixture(464);

    await store.settleSO(quotation);
    assert.equal(notices.length, 0);
});

test('prints all current order lines without converting or removing the ticket', async () => {
    const { button, calls, downloads } = fixture();
    const order = button.pos.getOrder();
    button.pos.lastCreatedSaleQuotation = { id: 99 };
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

test('checks branch availability for the selected line and its tint formula without saving', async () => {
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
    let dialogProps;
    button.dialog.add = (component, props) => {
        assert.equal(component, BranchStockDialog);
        dialogProps = props;
    };
    button.onClickQuotationStock();
    assert.equal(calls.length, 0);
    assert.equal(dialogProps.entries.length, 3);
    for (const entry of dialogProps.entries) {
        assert.equal(await dialogProps.loadStock(entry), stockRows);
    }
    assert.deepEqual(calls, [
        ['product.product', 'get_pos_quotation_branch_stock', '[7,{"2":1},{}]'],
        ['product.product', 'get_pos_quotation_branch_stock', '[7,{"2":2},{"3":24}]'],
        ['product.product', 'get_pos_quotation_branch_stock', '[7,{"2":2},{"3":4,"5":16}]'],
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
