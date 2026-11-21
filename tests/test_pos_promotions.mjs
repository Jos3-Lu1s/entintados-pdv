import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const root = new URL('../static/src/app/', import.meta.url);
const utility = fs.readFileSync(new URL('utils/tint_order.js', root), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
const helpers = new Function(utility + '\nreturn { isTintColorant, isTintedBase, tintParent, tintDose };')();

function patch(target, extension) {
    Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
}

function createPosLoyaltyContext() {
    class MockPosOrder {
        constructor(lines = []) {
            this.lines = lines;
        }
        getOrderlines() {
            return this.lines;
        }
        _getRewardLineValuesProduct(args) {
            return [{
                product_id: args.product || args.reward.reward_product_id,
                qty: 1,
                price_unit: 0,
            }];
        }
    }

    class MockControlButtons {
        async _applyReward(reward, coupon_id, potentialQty) {
            return { applied: true };
        }
    }

    const source = fs.readFileSync(new URL('overrides/pos_loyalty.js', root), 'utf8')
        .replace(/^import .*;\r?\n/gm, '');

    const context = {
        PosOrder: MockPosOrder,
        ControlButtons: MockControlButtons,
        patch,
        _t: (s) => s,
        isTintColorant: helpers.isTintColorant,
        console,
    };

    vm.runInNewContext(source, context);
    return { MockPosOrder, MockControlButtons };
}

test('pos_loyalty: exclusion de lineas protegidas', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const order = new MockPosOrder();
    const reward = { discount: 20 };

    // 1. Linea normal elegible
    const normalLine = {
        getQuantity: () => 1,
        price_unit: 100,
        fixed_price_locked: false,
        pricing_rule_type: 'none',
        product_id: { id: 10, tint_role: 'none' },
    };
    assert.equal(order._isLineExcludedFromPromo(normalLine, reward), false);

    // 2. Linea con precio pactado bloqueado
    const fixedPriceLine = {
        getQuantity: () => 1,
        price_unit: 100,
        fixed_price_locked: true,
        pricing_rule_type: 'fixed_price',
    };
    assert.equal(order._isLineExcludedFromPromo(fixedPriceLine, reward), true);

    // 3. Linea de colorante de entintado
    const colorantLine = {
        getQuantity: () => 1,
        price_unit: 50,
        product_id: { id: 20, tint_role: 'colorant' },
        is_tint_colorant: true,
    };
    assert.equal(order._isLineExcludedFromPromo(colorantLine, reward), true);

    // 4. Componente de paquete combo
    const comboChildLine = {
        getQuantity: () => 1,
        price_unit: 80,
        combo_parent_id: { id: 99 },
    };
    assert.equal(order._isLineExcludedFromPromo(comboChildLine, reward), true);

    // 5. Acuerdo comercial con descuento mayor o igual a la promocion
    const higherAgreementLine = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 25,
        pricing_rule_type: 'partner_rule',
    };
    assert.equal(order._isLineExcludedFromPromo(higherAgreementLine, reward), true);

    const equalAgreementLine = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 20,
        pricing_rule_type: 'scheme_rule',
    };
    assert.equal(order._isLineExcludedFromPromo(equalAgreementLine, reward), true);

    // 6. Acuerdo comercial con descuento menor a la promocion (no excluido, sustitucion diferencial)
    const lowerAgreementLine = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 15,
        pricing_rule_type: 'partner_rule',
    };
    assert.equal(order._isLineExcludedFromPromo(lowerAgreementLine, reward), false);
});

test('pos_loyalty: calculo de factor diferencial de sustitucion', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const order = new MockPosOrder();

    const lineWithoutAgreement = {
        discount: 0,
        pricing_rule_type: 'none',
    };
    assert.equal(order._getPromoEffectiveLineFactor(lineWithoutAgreement, { discount: 20 }), 1.0);

    const lineWithAgreement = {
        discount: 15,
        pricing_rule_type: 'partner_rule',
    };
    // Factor = (20 - 15) / 20 = 0.25
    assert.equal(order._getPromoEffectiveLineFactor(lineWithAgreement, { discount: 20 }), 0.25);

    const lineWithSmallerAgreement = {
        discount: 10,
        pricing_rule_type: 'scheme_rule',
    };
    // Factor = (30 - 10) / 30 = 20 / 30
    assert.ok(Math.abs(order._getPromoEffectiveLineFactor(lineWithSmallerAgreement, { discount: 30 }) - (2 / 3)) < 1e-6);
});

test('pos_loyalty: base descontable en promocion de orden completa', () => {
    const { MockPosOrder } = createPosLoyaltyContext();

    const lines = [
        {
            uuid: 'L1',
            getQuantity: () => 1,
            price_unit: 1000,
            basePrice: 1000,
            prices: { total_included: 1000 },
            fixed_price_locked: false,
            pricing_rule_type: 'none',
            product_id: { id: 1, tint_role: 'none' },
            tax_ids: [{ id: 1, amount_type: 'percent' }],
        },
        {
            uuid: 'L2',
            getQuantity: () => 1,
            price_unit: 500,
            basePrice: 500,
            prices: { total_included: 500 },
            fixed_price_locked: true,
            pricing_rule_type: 'fixed_price',
            product_id: { id: 2, tint_role: 'none' },
            tax_ids: [{ id: 1, amount_type: 'percent' }],
        },
        {
            uuid: 'L3',
            getQuantity: () => 1,
            price_unit: 50,
            basePrice: 50,
            prices: { total_included: 50 },
            product_id: { id: 3, tint_role: 'colorant' },
            is_tint_colorant: true,
            tax_ids: [{ id: 1, amount_type: 'percent' }],
        },
        {
            uuid: 'L4',
            getQuantity: () => 1,
            price_unit: 100,
            basePrice: 100,
            prices: { total_included: 100 },
            combo_parent_id: { id: 10 },
            product_id: { id: 4, tint_role: 'none' },
            tax_ids: [{ id: 1, amount_type: 'percent' }],
        },
        {
            uuid: 'L5',
            getQuantity: () => 1,
            price_unit: 1000,
            discount: 15,
            pricing_rule_type: 'partner_rule',
            basePrice: 850,
            prices: { total_included: 850 },
            product_id: { id: 5, tint_role: 'none' },
            tax_ids: [{ id: 1, amount_type: 'percent' }],
        },
    ];

    const order = new MockPosOrder(lines);
    const reward = {
        discount: 20,
        program_id: { program_type: 'promotion' },
    };

    const result = order._getDiscountableOnOrder(reward);
    // Base esperada:
    // L1: 1000 * 1.0 = 1000
    // L2: excluido (precio fijo) = 0
    // L3: excluido (colorante) = 0
    // L4: excluido (combo) = 0
    // L5: 850 * (20 - 15) / 20 = 850 * 0.25 = 212.5
    // Total = 1212.5
    assert.equal(result.discountable, 1212.5);
    assert.equal(result.discountablePerTax['1'], 1212.5);
});

test('pos_loyalty: rechazo de bases entintadas y colorantes en regalos', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const order = new MockPosOrder();

    // Recompensa con producto formulado base
    const baseReward = {
        reward_type: 'product',
        reward_product_id: { id: 100, tint_role: 'base', display_name: 'Base Blanca' },
    };
    const baseResult = order._getRewardLineValuesProduct({ reward: baseReward });
    assert.equal(typeof baseResult, 'string');
    assert.match(baseResult, /no son elegibles como productos de regalo/);

    // Recompensa con producto formulado colorante
    const colorantReward = {
        reward_type: 'product',
        reward_product_id: { id: 101, tint_role: 'colorant', display_name: 'Colorante Negro' },
    };
    const colorantResult = order._getRewardLineValuesProduct({ reward: colorantReward });
    assert.equal(typeof colorantResult, 'string');
    assert.match(colorantResult, /no son elegibles como productos de regalo/);

    // Recompensa con producto estandar
    const standardReward = {
        reward_type: 'product',
        reward_product_id: { id: 102, tint_role: 'none', display_name: 'Brocha 2 pulg' },
        program_id: { name: 'Promo Brocha de Regalo' },
    };
    const validResult = order._getRewardLineValuesProduct({ reward: standardReward });
    assert.ok(Array.isArray(validResult));
    assert.equal(validResult.length, 1);
    assert.equal(validResult[0].price_origin, 'promo');
    assert.equal(validResult[0].pricing_rule_type, 'promo');
    assert.equal(validResult[0].promo_reward_id, standardReward);
});

test('pos_loyalty: control buttons valida regalo multi-producto sin componentes formulados', async () => {
    const { MockControlButtons } = createPosLoyaltyContext();
    const ctrl = new MockControlButtons();
    let notificationAdded = false;
    ctrl.notification = {
        add: (msg, opts) => {
            notificationAdded = true;
            assert.match(msg, /no son elegibles como productos de regalo/);
        },
    };

    // 1. Recompensa multi-producto donde todos son formulados (bases/colorantes)
    const formulaOnlyReward = {
        reward_type: 'product',
        multi_product: true,
        reward_product_ids: [
            { id: 1, tint_role: 'base' },
            { id: 2, tint_role: 'colorant' },
        ],
    };
    const rejectedResult = await ctrl._applyReward(formulaOnlyReward, false, 1);
    assert.equal(rejectedResult, false);
    assert.equal(notificationAdded, true);

    // 2. Recompensa multi-producto con al menos un producto valido (no formulado)
    const mixedReward = {
        reward_type: 'product',
        multi_product: true,
        reward_product_ids: [
            { id: 1, tint_role: 'base' },
            { id: 3, tint_role: 'none' },
        ],
    };
    const approvedResult = await ctrl._applyReward(mixedReward, false, 1);
    assert.deepEqual(approvedResult, { applied: true });
});
