import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/overrides/orderline.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '')
    .replace(/export const /g, 'var ')
    .replace(/export (function|class)/g, '$1');

function fixture() {
    class BaseOrderline {
        setup() {}
        get lineScreenValues() {
            return { price: '100.00', displayPriceUnit: true, name: 'Producto A' };
        }
    }
    class BaseComponent {}

    const sandbox = {
        Component: BaseComponent,
        Orderline: BaseOrderline,
        _t: (text) => text,
        formatCurrency: (amount) => String(amount),
        usePopover: (comp, opts) => {
            const hook = {
                openedTarget: null,
                openedProps: null,
                isOpen: false,
                open(target, props) {
                    hook.isOpen = true;
                    hook.openedTarget = target;
                    hook.openedProps = props;
                },
                close() {
                    hook.isOpen = false;
                },
            };
            return hook;
        },
        tintedProductName: () => null,
        patch(target, extension) {
            const proto = target.prototype || target;
            Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(proto), Object.getOwnPropertyDescriptors(proto)));
            Object.defineProperties(proto, Object.getOwnPropertyDescriptors(extension));
        },
    };

    vm.runInNewContext(source, sandbox);
    return {
        Orderline: BaseOrderline,
        POS_PRICE_ORIGINS: sandbox.POS_PRICE_ORIGINS,
        getPriceOriginData: sandbox.getPriceOriginData,
        PosPriceOriginPopover: sandbox.PosPriceOriginPopover,
    };
}

test('POS_PRICE_ORIGINS maps all expected origin mechanisms with icons and colors', () => {
    const { POS_PRICE_ORIGINS } = fixture();
    const origins = JSON.parse(JSON.stringify(POS_PRICE_ORIGINS));
    assert.deepEqual(origins.list, { icon: 'fa-tag', color: 'text-muted', title: 'Precio de venta' });
    assert.deepEqual(origins.pricelist, { icon: 'fa-list-alt', color: 'text-info', title: 'Tarifa' });
    assert.deepEqual(origins.agreement, { icon: 'fa-lock', color: 'text-success', title: 'Acuerdo comercial' });
    assert.deepEqual(origins.fixed_price, { icon: 'fa-lock', color: 'text-success', title: 'Precio fijo de acuerdo' });
    assert.deepEqual(origins.manual, { icon: 'fa-pencil', color: 'text-warning', title: 'Manual' });
    assert.deepEqual(origins.promo, { icon: 'fa-star', color: 'text-primary', title: 'Promoción' });
    assert.deepEqual(origins.combo, { icon: 'fa-cubes', color: 'text-primary', title: 'Parte de combo' });
    assert.deepEqual(origins.tint, { icon: 'fa-tint', color: 'text-info', title: 'Entintado' });
});

test('getPriceOriginData extracts custom label and falls back cleanly', () => {
    const { getPriceOriginData } = fixture();
    const lineWithAgreement = {
        price_origin: 'agreement',
        price_origin_label: 'Desc. Línea: Esmaltes (10.0%)',
    };
    const resAgreement = getPriceOriginData(lineWithAgreement);
    assert.equal(resAgreement.icon, 'fa-lock');
    assert.equal(resAgreement.color, 'text-success');
    assert.equal(resAgreement.title, 'Acuerdo comercial');
    assert.equal(resAgreement.detail, 'Desc. Línea: Esmaltes (10.0%)');

    const lineStandard = { price_origin: 'list' };
    const resStandard = getPriceOriginData(lineStandard);
    assert.equal(resStandard.icon, 'fa-tag');
    assert.equal(resStandard.color, 'text-muted');
    assert.equal(resStandard.title, 'Precio de venta');
    assert.equal(resStandard.detail, 'Precio de venta del producto');

    const lineManual = { price_origin: 'manual' };
    const resManual = getPriceOriginData(lineManual);
    assert.equal(resManual.icon, 'fa-pencil');
    assert.equal(resManual.color, 'text-warning');
    assert.equal(resManual.title, 'Manual');
    assert.equal(resManual.detail, 'Modificado de forma manual');

    const lineTint = { price_origin: 'tint' };
    const resTint = getPriceOriginData(lineTint);
    assert.equal(resTint.icon, 'fa-tint');
    assert.equal(resTint.color, 'text-info');
    assert.equal(resTint.title, 'Entintado');
    assert.equal(resTint.detail, 'Precio del entintado: base más colorantes');
});

test('getPriceOriginData handles legacy and unclassified lines with smart fallback', () => {
    const { getPriceOriginData } = fixture();
    assert.equal(getPriceOriginData(null), null);

    const lockedLine = { fixed_price_locked: true, pricing_rule_origin: 'Precio contractual' };
    const resLocked = getPriceOriginData(lockedLine);
    assert.equal(resLocked.key, 'fixed_price');
    assert.equal(resLocked.icon, 'fa-lock');
    assert.equal(resLocked.detail, 'Precio contractual');

    const agreementLine = { price_origin: 'agreement', pricing_rule_origin: 'Desc. Global Cliente (5.0%)' };
    const resAgreement = getPriceOriginData(agreementLine);
    assert.equal(resAgreement.key, 'agreement');
    assert.equal(resAgreement.icon, 'fa-lock');
    assert.equal(resAgreement.detail, 'Desc. Global Cliente (5.0%)');

    const genericLine = {};
    const resGeneric = getPriceOriginData(genericLine);
    assert.equal(resGeneric.key, 'list');
    assert.equal(resGeneric.icon, 'fa-tag');
});

test('lineScreenValues exposes priceOrigin properties in display mode and omits them in receipt mode', () => {
    const { Orderline } = fixture();

    const instanceDisplay = new Orderline();
    instanceDisplay.props = { mode: 'display', basic_receipt: false };
    instanceDisplay.line = {
        qty: 1,
        currency: { id: 1 },
        priceExcl: 200,
        priceExclNoDiscount: 200,
        price_origin: 'pricelist',
        price_origin_label: 'Tarifa: Mayoreo',
    };
    const valsDisplay = instanceDisplay.lineScreenValues;
    assert.equal(valsDisplay.priceOriginIcon, 'fa-list-alt');
    assert.equal(valsDisplay.priceOriginColor, 'text-info');
    assert.equal(valsDisplay.priceOriginTitle, 'Tarifa');
    assert.equal(valsDisplay.priceOriginDetail, 'Tarifa: Mayoreo');

    const instanceReceipt = new Orderline();
    instanceReceipt.props = { mode: 'receipt', basic_receipt: true };
    instanceReceipt.line = { ...instanceDisplay.line };
    const valsReceipt = instanceReceipt.lineScreenValues;
    assert.equal(valsReceipt.priceOriginIcon, undefined);
    assert.equal(valsReceipt.priceOriginTitle, undefined);
});

test('onPriceOriginClick stops propagation and opens popover with line detail', () => {
    const { Orderline } = fixture();
    const instance = new Orderline();
    instance.setup();
    instance.props = { mode: 'display', basic_receipt: false };
    instance.line = {
        price_origin: 'fixed_price',
        price_origin_label: 'Precio Fijo: 150.00',
    };

    let stopped = false;
    const mockEvent = {
        stopPropagation() { stopped = true; },
        currentTarget: { id: 'icon-element' },
    };

    instance.onPriceOriginClick(mockEvent, instance.line);
    assert.equal(stopped, true);
    assert.equal(instance.priceOriginPopover.isOpen, true);
    assert.equal(instance.priceOriginPopover.openedProps.title, 'Precio fijo de acuerdo');
    assert.equal(instance.priceOriginPopover.openedProps.detail, 'Precio Fijo: 150.00');

    instance.onPriceOriginClick(mockEvent, instance.line);
    assert.equal(instance.priceOriginPopover.isOpen, false);
});

test('lineScreenValues resolves colors and icons for agreement, promo, fixed_price, and manual discounts', () => {
    const { Orderline } = fixture();

    // 1. Acuerdo comercial de descuento
    const instAgreement = new Orderline();
    instAgreement.props = { mode: 'display', basic_receipt: false };
    instAgreement.line = {
        qty: 1,
        currency: { id: 1 },
        priceExcl: 180,
        priceExclNoDiscount: 200,
        price_origin: 'pricelist',
        price_origin_label: 'Tarifa: Mayoreo — Producto X: 200.00',
        pricing_rule_type: 'line',
        pricing_rule_origin: 'Desc. Línea: Esmaltes (10.0%)',
    };
    const valsAgreement = instAgreement.lineScreenValues;
    assert.equal(valsAgreement.priceOriginIcon, 'fa-list-alt');
    assert.equal(valsAgreement.priceOriginColor, 'text-info');
    assert.equal(valsAgreement.pricingRuleColor, 'text-info');
    assert.equal(valsAgreement.pricingRuleIcon, 'fa-tag');

    // 2. Precio fijo pactado
    const instFixed = new Orderline();
    instFixed.props = { mode: 'display', basic_receipt: false };
    instFixed.line = {
        qty: 1,
        currency: { id: 1 },
        priceExcl: 120,
        priceExclNoDiscount: 120,
        price_origin: 'fixed_price',
        price_origin_label: 'Precio Fijo: Cliente (120.00)',
        pricing_rule_type: 'fixed_price',
        pricing_rule_origin: 'Precio Fijo: Cliente (120.00)',
    };
    const valsFixed = instFixed.lineScreenValues;
    assert.equal(valsFixed.priceOriginIcon, 'fa-lock');
    assert.equal(valsFixed.priceOriginColor, 'text-success');
    assert.equal(valsFixed.pricingRuleColor, 'text-success');
    assert.equal(valsFixed.pricingRuleIcon, 'fa-lock');

    // 3. Promoción aplicada
    const instPromo = new Orderline();
    instPromo.props = { mode: 'display', basic_receipt: false };
    instPromo.line = {
        qty: 1,
        currency: { id: 1 },
        priceExcl: 150,
        priceExclNoDiscount: 200,
        price_origin: 'list',
        price_origin_label: 'Precio de venta del producto',
        pricing_rule_type: 'promo',
        pricing_rule_origin: 'Promoción Buen Fin - 25%',
        promo_reward_id: 1,
    };
    const valsPromo = instPromo.lineScreenValues;
    assert.equal(valsPromo.priceOriginIcon, 'fa-tag');
    assert.equal(valsPromo.priceOriginColor, 'text-muted');
    assert.equal(valsPromo.pricingRuleColor, 'text-primary');
    assert.equal(valsPromo.pricingRuleIcon, 'fa-star');

    // 4. Descuento manual
    const instManual = new Orderline();
    instManual.props = { mode: 'display', basic_receipt: false };
    instManual.line = {
        qty: 1,
        currency: { id: 1 },
        priceExcl: 190,
        priceExclNoDiscount: 200,
        price_origin: 'manual',
        price_origin_label: 'Precio unitario tecleado manualmente',
        pricing_rule_type: 'manual',
        pricing_rule_origin: 'Desc. Manual (5.0%)',
    };
    const valsManual = instManual.lineScreenValues;
    assert.equal(valsManual.priceOriginIcon, 'fa-pencil');
    assert.equal(valsManual.priceOriginColor, 'text-warning');
    assert.equal(valsManual.pricingRuleColor, 'text-warning');
    assert.equal(valsManual.pricingRuleIcon, 'fa-pencil');
});

