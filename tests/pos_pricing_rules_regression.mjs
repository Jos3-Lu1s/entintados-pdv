import fs from 'node:fs';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('../static/src/app/utils/pricing_rules.js', import.meta.url), 'utf8')
    .replace(/^export function /m, 'function ');
const getPartnerPricingRule = new Function(`${source}\nreturn getPartnerPricingRule;`)();

const goods = { id: 1, type: 'consu' };
const service = { id: 2, type: 'service' };

function models(rules) {
    return { 'res.partner.discount.rule': { getAll: () => rules } };
}

function productRules(partner, values) {
    return [goods, service].map((product, index) => ({
        id: index + 1, partner_id: partner.id, applied_on: '0_product', product_id: product.id, ...values,
    }));
}

function assertSameRule(label, pos, partner) {
    const forGoods = getPartnerPricingRule(pos, partner, goods);
    const forService = getPartnerPricingRule(pos, partner, service);
    for (const key of ['type', 'price', 'discount']) {
        assert.equal(forService[key], forGoods[key], `${label}: ${key}`);
    }
    return forService;
}

const partner = { id: 10, discount: 0 };

const fixed = assertSameRule('precio fijo',
    models(productRules(partner, { rule_type: 'fixed_price', fixed_price: 80 })), partner);
assert.equal(fixed.type, 'fixed_price');
assert.equal(fixed.price, 80);

const productDiscount = assertSameRule('descuento por producto',
    models(productRules(partner, { rule_type: 'discount', discount: 10 })), partner);
assert.equal(productDiscount.type, 'discount');
assert.equal(productDiscount.discount, 10);

const globalPartner = { id: 11, discount: 0.1 };
const globalDiscount = assertSameRule('descuento global', models([]), globalPartner);
assert.equal(globalDiscount.type, 'discount');
assert.equal(globalDiscount.originType, 'global');
assert.equal(globalDiscount.discount, 10);

console.log('pos_pricing_rules_regression: ok');
