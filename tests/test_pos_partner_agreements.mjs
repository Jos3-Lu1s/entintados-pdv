import fs from 'node:fs';
import assert from 'node:assert/strict';

// -------------------------------------------------------------
// 1. Probar pricing_rules.js (conversión de UdM, moneda y priceOrigin)
// -------------------------------------------------------------
const pricingRulesSource = fs.readFileSync(
    new URL('../static/src/app/utils/pricing_rules.js', import.meta.url),
    'utf8'
).replace(/^export function /m, 'function ');
const getPartnerPricingRule = new Function(`${pricingRulesSource}\nreturn getPartnerPricingRule;`)();

const uomLitre = { id: 1, name: 'L', category_id: { id: 10 }, factor: 1.0 };
const uomGallon = { id: 2, name: 'Galón', category_id: { id: 10 }, factor: 1 / 3.785 };
const uomBox = { id: 3, name: 'Caja', category_id: { id: 20 }, factor: 1.0 };

const currencyMXN = { id: 1, name: 'MXN', rate: 1.0 };
const currencyUSD = { id: 2, name: 'USD', rate: 0.05 };

const productA = {
    id: 101,
    name: 'Pintura Blanca',
    uom_id: uomLitre,
    lst_price: 150,
};

const partnerWithFixed = { id: 50 };
const partnerWithDiscount = { id: 51 };
const partnerNoRules = { id: 52 };

const fixedRule = {
    id: 1,
    partner_id: 50,
    applied_on: '0_product',
    rule_type: 'fixed_price',
    product_id: 101,
    fixed_price: 100,
};

const discountRule = {
    id: 2,
    partner_id: 51,
    applied_on: '0_product',
    rule_type: 'discount',
    product_id: 101,
    discount: 15,
};

const mockModels = {
    'res.partner.discount.rule': {
        getAll: () => [fixedRule, discountRule],
    },
    'uom.uom': {
        get: (id) => [uomLitre, uomGallon, uomBox].find((u) => u.id === id),
    },
    'res.currency': {
        get: (id) => [currencyMXN, currencyUSD].find((c) => c.id === id),
    },
};

const mockPos = {
    models: mockModels,
    currency: currencyMXN,
    company: { currency_id: currencyMXN },
};

// Test 1.1: Regla de precio fijo en misma UdM
const ruleSameUom = getPartnerPricingRule(mockPos, partnerWithFixed, productA);
assert.equal(ruleSameUom.type, 'fixed_price');
assert.equal(ruleSameUom.priceOrigin, 'agreement');
assert.equal(ruleSameUom.price, 100);

// Test 1.2: Conversión de UdM (Litro a Galón)
const ruleGallon = getPartnerPricingRule(mockPos, partnerWithFixed, productA, { uom: uomGallon });
assert.equal(ruleGallon.type, 'fixed_price');
assert.equal(ruleGallon.priceOrigin, 'agreement');
assert.equal(Math.round(ruleGallon.price * 100) / 100, 378.50);

// Test 1.3: UdM de distinta categoría no se escala (mantiene precio base)
const ruleDiffCat = getPartnerPricingRule(mockPos, partnerWithFixed, productA, { uom: uomBox });
assert.equal(ruleDiffCat.price, 100);

// Test 1.4: Regla de descuento por producto
const ruleDisc = getPartnerPricingRule(mockPos, partnerWithDiscount, productA);
assert.equal(ruleDisc.type, 'discount');
assert.equal(ruleDisc.priceOrigin, 'agreement');
assert.equal(ruleDisc.discount, 15);

// Test 1.5: Sin regla
const ruleNone = getPartnerPricingRule(mockPos, partnerNoRules, productA);
assert.equal(ruleNone.type, 'none');
assert.equal(ruleNone.priceOrigin, 'list');
assert.equal(ruleNone.discount, 0);

// -------------------------------------------------------------
// 2. Probar pos_order_line.js (Bloqueo de Numpad y serialización)
// -------------------------------------------------------------
function patch(target, extension) {
    Object.setPrototypeOf(
        extension,
        Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target))
    );
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
}

class FakePosOrderline {
    constructor() {
        this.qty = 1;
        this.price_unit = 100;
        this.discount = 0;
        this.fixed_price_locked = false;
        this.models = { env: { services: { notification: { add: (msg, opts) => { this.lastNotification = msg; } } } } };
    }
    setUnitPrice(price) {
        this.price_unit = price;
        return true;
    }
    setDiscount(discount) {
        this.discount = discount;
        return true;
    }
    getFullProductName() { return "Producto Test"; }
    canBeMergedWith() { return false; }
    delete() { return true; }
    export_as_JSON() { return {}; }
    init_from_JSON() {}
}

class FakePosOrder {
    constructor() {
        this.lines = [];
    }
    selectOrderline(l) { return l; }
    removeOrderline(l) { return true; }
    serializeForORM() { return {}; }
}

class FakeTicketScreen {
    onClickRefund() { return false; }
}

const posOrderLineSource = fs.readFileSync(
    new URL('../static/src/app/overrides/pos_order_line.js', import.meta.url),
    'utf8'
)
    .replace(/^import .*;\r?\n/gm, '')
    .replace(/isTintColorant\(this\)/g, 'false')
    .replace(/isTintedBase\(this\)/g, 'false')
    .replace(/isTintColorant\(orderline\)/g, 'false')
    .replace(/isTintedBase\(orderline\)/g, 'false')
    .replace(/tintedProductName\(this\)/g, '""');

new Function(
    'patch',
    '_t',
    'PosOrderline',
    'PosOrder',
    'TicketScreen',
    'AlertDialog',
    posOrderLineSource
)(
    patch,
    (str) => str,
    FakePosOrderline,
    FakePosOrder,
    FakeTicketScreen,
    class FakeAlertDialog {}
);

const line = new FakePosOrderline();

// Línea normal sin bloqueo: permite cambiar precio y descuento
assert.equal(line.setUnitPrice(120), true);
assert.equal(line.price_unit, 120);
assert.equal(line.setDiscount(10), true);
assert.equal(line.discount, 10);

// Activar bloqueo de precio fijo
line.fixed_price_locked = true;

// Intento manual de cambiar precio unitario debe fallar y notificar
const resPrice = line.setUnitPrice(90);
assert.equal(resPrice, false);
assert.equal(line.price_unit, 120, 'El precio no debe cambiar');
assert.equal(line.lastNotification, 'El precio está pactado por acuerdo comercial y no se puede modificar.');

// Intento manual de cambiar descuento debe fallar y notificar
const resDisc = line.setDiscount(5);
assert.equal(resDisc, false);
assert.equal(line.discount, 10, 'El descuento no debe cambiar');
assert.equal(line.lastNotification, 'Los productos con precio fijo pactado no admiten descuento.');

// Asignación programática interna con bypass debe permitirse
line._entintadosSettingPrice = true;
assert.equal(line.setUnitPrice(80), true);
assert.equal(line.price_unit, 80);
delete line._entintadosSettingPrice;

line._entintadosSettingDiscount = true;
assert.equal(line.setDiscount(0), true);
assert.equal(line.discount, 0);
delete line._entintadosSettingDiscount;

// Serialización de price_origin
line.price_origin = 'agreement';
line.price_origin_label = 'Precio Fijo';
line.pricing_rule_type = 'fixed_price';
line.pricing_rule_origin = 'Precio Fijo';

const json = line.export_as_JSON();
assert.equal(json.price_origin, 'agreement');
assert.equal(json.price_origin_label, 'Precio Fijo');

const ormData = line.serializeForORM();
assert.equal(ormData.price_origin, 'agreement');
assert.equal(ormData.price_origin_label, 'Precio Fijo');

console.log('test_pos_partner_agreements: Todos los casos pasaron correctamente.');
