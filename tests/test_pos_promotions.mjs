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
        _getRewardLineValuesDiscount(args) {
            return [{
                product_id: { id: 999 },
                qty: 1,
                price_unit: -10,
                is_reward_line: true,
            }];
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
        .replace(/^import .*;\r?\n/gm, '')
        .replace(/^export /gm, '');

    const context = {
        PosOrder: MockPosOrder,
        ControlButtons: MockControlButtons,
        SelectionPopup: class MockSelectionPopup {},
        Orderline: class MockOrderline {},
        patch,
        _t: (s) => s,
        isTintColorant: helpers.isTintColorant,
        getPartnerPricingRule: () => ({ type: 'none', discount: 0 }),
        console,
    };

    vm.runInNewContext(source, context);
    return {
        MockPosOrder,
        MockControlButtons,
        isLinePromotionReward: context.isLinePromotionReward,
    };
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

test('pos_loyalty: identificacion de promociones por linea directas', () => {
    const { isLinePromotionReward } = createPosLoyaltyContext();

    assert.equal(isLinePromotionReward(null), false);
    assert.equal(isLinePromotionReward({ reward_type: 'product' }), false);
    assert.equal(isLinePromotionReward({ reward_type: 'free_shipping' }), false);
    assert.equal(isLinePromotionReward({
        reward_type: 'discount',
        discount_mode: 'per_order',
        discount_applicability: 'order',
        program_id: { program_type: 'promotion' },
    }), false);
    assert.equal(isLinePromotionReward({
        reward_type: 'discount',
        discount_mode: 'percent',
        discount_applicability: 'order',
        discount_max_amount: 100,
        program_id: { program_type: 'promotion' },
    }), false);
    assert.equal(isLinePromotionReward({
        reward_type: 'discount',
        discount_mode: 'percent',
        discount_applicability: 'cheapest',
        program_id: { program_type: 'promotion' },
    }), false);
    assert.equal(isLinePromotionReward({
        reward_type: 'discount',
        discount_mode: 'percent',
        discount_applicability: 'order',
        program_id: { program_type: 'loyalty' },
    }), false);

    assert.equal(isLinePromotionReward({
        reward_type: 'discount',
        discount_mode: 'percent',
        discount_applicability: 'order',
        program_id: { program_type: 'promotion' },
    }), true);
    assert.equal(isLinePromotionReward({
        reward_type: 'discount',
        discount_mode: 'percent',
        discount_applicability: 'specific',
        program_id: { program_type: 'promotion' },
    }), true);
});

test('pos_loyalty: supresion de lineas auxiliares de descuento para promociones directas', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const order = new MockPosOrder();

    const percentReward = {
        reward_type: 'discount',
        discount_mode: 'percent',
        discount_applicability: 'order',
        program_id: { program_type: 'promotion' },
        discount: 20,
    };
    const emptyLines = order._getRewardLineValuesDiscount({ reward: percentReward });
    assert.equal(emptyLines.length, 0, 'Las promociones directas por linea no deben generar lineas auxiliares negativas');

    const fixedReward = {
        reward_type: 'discount',
        discount_mode: 'per_order',
        discount_applicability: 'order',
        program_id: { program_type: 'promotion' },
        discount: 50,
    };
    const fallbackLines = order._getRewardLineValuesDiscount({ reward: fixedReward });
    assert.equal(fallbackLines.length, 1);
    assert.equal(fallbackLines[0].is_reward_line, true);
});

test('pos_loyalty: aplicacion directa de promocion sobre descuento de linea sin agregar lineas', () => {
    const { MockPosOrder } = createPosLoyaltyContext();

    const normalLine = {
        uuid: 'L1',
        getQuantity: () => 1,
        price_unit: 100,
        discount: 0,
        pricing_rule_type: 'none',
        product_id: { id: 10, tint_role: 'none' },
    };
    const lowerAgreementLine = {
        uuid: 'L2',
        getQuantity: () => 1,
        price_unit: 200,
        discount: 10,
        pricing_rule_type: 'partner_rule',
        product_id: { id: 11, tint_role: 'none' },
    };
    const higherAgreementLine = {
        uuid: 'L3',
        getQuantity: () => 1,
        price_unit: 300,
        discount: 30,
        pricing_rule_type: 'partner_rule',
        product_id: { id: 12, tint_role: 'none' },
    };
    const fixedPriceLine = {
        uuid: 'L4',
        getQuantity: () => 1,
        price_unit: 400,
        discount: 0,
        fixed_price_locked: true,
        pricing_rule_type: 'fixed_price',
        product_id: { id: 13, tint_role: 'none' },
    };
    const colorantLine = {
        uuid: 'L5',
        getQuantity: () => 1,
        price_unit: 50,
        discount: 0,
        product_id: { id: 14, tint_role: 'colorant' },
        is_tint_colorant: true,
    };

    const lines = [normalLine, lowerAgreementLine, higherAgreementLine, fixedPriceLine, colorantLine];
    const order = new MockPosOrder(lines);

    const promoReward = {
        id: 77,
        reward_type: 'discount',
        discount_mode: 'percent',
        discount: 25,
        program_id: { name: 'Buen fin' },
        reward_target_label: 'Línea: Vinil-Acrilica',
    };

    const applied = order._applyLinePromotionDirect(promoReward);
    assert.equal(applied, true);
    assert.equal(order.lines.length, 5, 'No se deben agregar lineas adicionales');

    // Linea normal debe recibir el descuento, etiqueta y origen de promocion
    assert.equal(normalLine.discount, 25);
    assert.equal(normalLine.pricing_rule_type, 'promo');
    assert.equal(normalLine.pricing_rule_origin, 'Buen fin · Línea: Vinil-Acrilica - 25%');
    assert.equal(normalLine.price_origin, 'promo');
    assert.deepEqual(normalLine.promo_reward_id, promoReward);

    // Linea con acuerdo menor debe ser sustituida por el descuento mayor de promocion
    assert.equal(lowerAgreementLine.discount, 25);
    assert.equal(lowerAgreementLine.pricing_rule_type, 'promo');
    assert.equal(lowerAgreementLine.price_origin, 'promo');
    assert.deepEqual(lowerAgreementLine.promo_reward_id, promoReward);

    // Linea con acuerdo comercial superior debe conservar su acuerdo original
    assert.equal(higherAgreementLine.discount, 30);
    assert.equal(higherAgreementLine.pricing_rule_type, 'partner_rule');
    assert.notEqual(higherAgreementLine.promo_reward_id, promoReward);

    // Linea de precio pactado fijo no debe cambiar
    assert.equal(fixedPriceLine.discount, 0);
    assert.equal(fixedPriceLine.pricing_rule_type, 'fixed_price');

    // Colorante no debe cambiar
    assert.equal(colorantLine.discount, 0);
});

test('pos_loyalty: alternancia libre entre promociones (mayor o menor descuento)', () => {
    const { MockPosOrder } = createPosLoyaltyContext();

    const line = {
        uuid: 'L1',
        getQuantity: () => 1,
        price_unit: 100,
        discount: 25,
        pricing_rule_type: 'promo',
        pricing_rule_origin: 'Promo Alta (25%)',
        promo_reward_id: { id: 1, discount: 25 },
        product_id: { id: 10, tint_role: 'none' },
    };
    const order = new MockPosOrder([line]);

    const lowerPromoReward = {
        id: 2,
        reward_type: 'discount',
        discount_mode: 'percent',
        discount: 15,
        program_id: { name: 'Promo Menor' },
        reward_target_label: 'Línea: Vinil-Acrilica',
    };

    // Alternar a promocion de menor descuento debe permitirse si el usuario la selecciona
    const applied = order._applyLinePromotionDirect(lowerPromoReward);
    assert.equal(applied, true);
    assert.equal(line.discount, 15);
    assert.equal(line.pricing_rule_origin, 'Promo Menor · Línea: Vinil-Acrilica - 15%');
    assert.equal(line.promo_reward_id.id, 2);
});

test('pos_loyalty: dejar sin promocion una linea y restaurar acuerdo comercial', () => {
    const { MockPosOrder } = createPosLoyaltyContext();

    const line = {
        uuid: 'L1',
        getQuantity: () => 1,
        price_unit: 100,
        discount: 20,
        pricing_rule_type: 'promo',
        pricing_rule_origin: 'Promo (20%)',
        price_origin: 'promo',
        promo_reward_id: { id: 10 },
        product_id: { id: 55, tint_role: 'none' },
    };
    const order = new MockPosOrder([line]);
    order.order_promo_reward_id = { id: 10 };

    order._dropLinePromotion(line);

    assert.equal(line.promo_reward_id, false);
    assert.equal(line.pricing_rule_type, 'none');
    assert.equal(line.pricing_rule_origin, '');
    assert.equal(line.price_origin, 'list');
    assert.equal(line.discount, 0);
    assert.equal(order.order_promo_reward_id, false);
});

test('pos_loyalty: clickRewards getPayload maneja accion isRemoveAction sin error de undefined reward', async () => {
    const { MockPosOrder, MockControlButtons } = createPosLoyaltyContext();

    const line = {
        uuid: 'L1',
        getQuantity: () => 1,
        price_unit: 100,
        discount: 20,
        pricing_rule_type: 'promo',
        promo_reward_id: { id: 10 },
        product_id: { id: 55, tint_role: 'none' },
    };
    const order = new MockPosOrder([line]);
    order.getSelectedOrderline = () => line;
    order._getLineCommercialAgreementDiscount = () => 0;

    const ctrl = new MockControlButtons();
    ctrl.pos = {
        getOrder: () => order,
    };
    ctrl.getPotentialRewards = () => [
        {
            reward: {
                id: 10,
                program_id: { name: 'Promo Test' },
                discount: 20,
                description: '20% desc',
            },
            coupon_id: false,
            potentialQty: 1,
        },
    ];

    let popupConfig = null;
    ctrl.dialog = {
        add: (popupClass, config) => {
            popupConfig = config;
        },
    };

    await ctrl.clickRewards();
    assert.ok(popupConfig, 'El popup debe configurarse');
    assert.ok(popupConfig.list.length >= 2, 'Debe incluir opcion de remocion y opcion de recompensa');

    const removeOption = popupConfig.list.find((opt) => opt.id === 'remove_selected_line_promo');
    assert.ok(removeOption, 'Debe existir la opcion remove_selected_line_promo');
    assert.equal(removeOption.item.isRemoveAction, true);

    // Simular que SelectionPopup entrega selected.item a getPayload
    assert.doesNotThrow(() => {
        popupConfig.getPayload(removeOption.item);
    });

    assert.equal(line.promo_reward_id, false, 'La promocion debe quedar retirada');
    assert.equal(line.discount, 0);
});

test('pos_loyalty: asignacion individual de promocion por partida', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const lineA = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 10,
        pricing_rule_type: 'partner_rule',
        product_id: { id: 10 },
        setDiscount(d) { this.discount = d; },
    };
    const lineB = {
        getQuantity: () => 1,
        price_unit: 200,
        discount: 10,
        pricing_rule_type: 'partner_rule',
        product_id: { id: 20 },
        setDiscount(d) { this.discount = d; },
    };
    const order = new MockPosOrder([lineA, lineB]);

    const reward = {
        id: 55,
        program_id: { name: 'Promo 25%' },
        discount: 25,
        discount_applicability: 'specific',
    };

    const applied = order._applySingleLinePromotion(lineA, reward);
    assert.equal(applied, true);
    assert.equal(lineA.discount, 25);
    assert.equal(lineA.pricing_rule_type, 'promo');
    assert.equal(lineA.price_origin, 'promo');
    assert.equal(lineA.promo_reward_id, reward);

    assert.equal(lineB.discount, 10);
    assert.equal(lineB.pricing_rule_type, 'partner_rule');
    assert.equal(lineB.promo_reward_id, undefined);
});

test('pos_loyalty: exclusion de lineas con precio fijo o colorantes en asignacion individual', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const fixedLine = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 0,
        fixed_price_locked: true,
        pricing_rule_type: 'fixed_price',
        product_id: { id: 10 },
    };
    const colorantLine = {
        getQuantity: () => 1,
        price_unit: 50,
        discount: 0,
        is_tint_colorant: true,
        product_id: { id: 20, tint_role: 'colorant' },
    };
    const order = new MockPosOrder([fixedLine, colorantLine]);
    const reward = { id: 1, discount: 20 };

    assert.equal(order._applySingleLinePromotion(fixedLine, reward), false);
    assert.equal(order._applySingleLinePromotion(colorantLine, reward), false);
});

test('pos_loyalty: restaurar acuerdo comercial al retirar promocion de la linea', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const line = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 25,
        pricing_rule_type: 'promo',
        price_origin: 'promo',
        promo_reward_id: { id: 55, discount: 25 },
        product_id: { id: 10 },
        setDiscount(d) { this.discount = d; },
    };
    const order = new MockPosOrder([line]);
    order.getPartner = () => ({ id: 5 });
    order.models = {};

    order._dropLinePromotion(line);
    assert.equal(line.promo_reward_id, false);
    assert.equal(line.discount, 0);
    assert.equal(line.pricing_rule_type, 'none');
    assert.equal(line.price_origin, 'list');
});

test('pos_loyalty: aplicacion de promocion de orden completa a lineas elegibles', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const line1 = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 0,
        pricing_rule_type: 'none',
        product_id: { id: 10 },
        setDiscount(d) { this.discount = d; },
    };
    const line2 = {
        getQuantity: () => 2,
        price_unit: 50,
        discount: 0,
        pricing_rule_type: 'none',
        product_id: { id: 20 },
        setDiscount(d) { this.discount = d; },
    };
    const fixedLine = {
        getQuantity: () => 1,
        price_unit: 80,
        discount: 0,
        fixed_price_locked: true,
        pricing_rule_type: 'fixed_price',
        product_id: { id: 30 },
        setDiscount(d) { this.discount = d; },
    };
    const colorantLine = {
        getQuantity: () => 1,
        price_unit: 20,
        discount: 0,
        is_tint_colorant: true,
        product_id: { id: 40, tint_role: 'colorant' },
        setDiscount(d) { this.discount = d; },
    };

    const order = new MockPosOrder([line1, line2, fixedLine, colorantLine]);
    const orderReward = {
        id: 77,
        program_id: { name: 'Gran Venta 15%' },
        discount: 15,
        discount_applicability: 'order',
    };

    const applied = order._applyLinePromotionDirect(orderReward);
    assert.equal(applied, true);
    assert.equal(order.order_promo_reward_id, orderReward);

    // Lineas normales elegibles toman el 15%
    assert.equal(line1.discount, 15);
    assert.equal(line1.pricing_rule_type, 'promo');
    assert.equal(line1.price_origin, 'promo');
    assert.equal(line1.promo_reward_id, orderReward);

    assert.equal(line2.discount, 15);
    assert.equal(line2.pricing_rule_type, 'promo');
    assert.equal(line2.price_origin, 'promo');
    assert.equal(line2.promo_reward_id, orderReward);

    // Lineas protegidas quedan excluidas
    assert.equal(fixedLine.discount, 0);
    assert.equal(fixedLine.pricing_rule_type, 'fixed_price');

    assert.equal(colorantLine.discount, 0);
    assert.equal(colorantLine.is_tint_colorant, true);
});

test('pos_loyalty: promocion de orden respeta linea con acuerdo comercial superior', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const lineWithBetterAgreement = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 20,
        pricing_rule_type: 'partner_rule',
        product_id: { id: 10 },
        setDiscount(d) { this.discount = d; },
    };
    const lineWithLowerAgreement = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 5,
        pricing_rule_type: 'partner_rule',
        product_id: { id: 20 },
        setDiscount(d) { this.discount = d; },
    };
    const order = new MockPosOrder([lineWithBetterAgreement, lineWithLowerAgreement]);
    order._getLineCommercialAgreementDiscount = (line) => line === lineWithBetterAgreement ? 20 : 5;

    const orderReward = {
        id: 88,
        program_id: { name: 'Promo Orden 15%' },
        discount: 15,
        discount_applicability: 'order',
    };

    const applied = order._applyLinePromotionDirect(orderReward);
    assert.equal(applied, true);

    // Linea con acuerdo 20% > 15% conserva su acuerdo comercial
    assert.equal(lineWithBetterAgreement.discount, 20);
    assert.equal(lineWithBetterAgreement.pricing_rule_type, 'partner_rule');

    // Linea con acuerdo 5% < 15% toma la promocion de orden de 15%
    assert.equal(lineWithLowerAgreement.discount, 15);
    assert.equal(lineWithLowerAgreement.pricing_rule_type, 'promo');
    assert.equal(lineWithLowerAgreement.promo_reward_id, orderReward);
});

test('pos_loyalty: promocion de orden no pisa promocion individual especifica superior', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const specificReward = {
        id: 91,
        program_id: { name: 'Promo Especial 25%' },
        discount: 25,
        discount_applicability: 'specific',
    };
    const lineWithSpecific = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 25,
        pricing_rule_type: 'promo',
        price_origin: 'promo',
        promo_reward_id: specificReward,
        product_id: { id: 10 },
        setDiscount(d) { this.discount = d; },
    };
    const normalLine = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 0,
        pricing_rule_type: 'none',
        product_id: { id: 20 },
        setDiscount(d) { this.discount = d; },
    };
    const order = new MockPosOrder([lineWithSpecific, normalLine]);

    const orderReward = {
        id: 92,
        program_id: { name: 'Promo Orden 15%' },
        discount: 15,
        discount_applicability: 'order',
    };

    order._applyLinePromotionDirect(orderReward);

    // Linea especifica de 25% no es degradada al 15%
    assert.equal(lineWithSpecific.discount, 25);
    assert.equal(lineWithSpecific.promo_reward_id, specificReward);

    // Linea comun toma el 15%
    assert.equal(normalLine.discount, 15);
    assert.equal(normalLine.promo_reward_id, orderReward);
});

test('pos_loyalty: _isRewardValidForOrder evalua montos y cantidades minimas', () => {
    const { MockPosOrder } = createPosLoyaltyContext();
    const line1 = {
        getQuantity: () => 2,
        price_unit: 100,
        discount: 0,
        product_id: { id: 10 },
    };
    const order = new MockPosOrder([line1]);

    const rewardWithAmount = {
        program_id: {
            rule_ids: [
                {
                    any_product: true,
                    minimum_amount: 300,
                    minimum_amount_tax_mode: 'excl',
                },
            ],
        },
    };
    // 2 * 100 = 200 < 300 -> falso
    assert.equal(order._isRewardValidForOrder(rewardWithAmount), false);

    // Subimos cantidad: 4 * 100 = 400 >= 300 -> verdadero
    line1.getQuantity = () => 4;
    assert.equal(order._isRewardValidForOrder(rewardWithAmount), true);
});

function createPosStoreContext() {
    class MockPosStore {}
    class MockPosOrder {
        constructor(lines = []) {
            this.lines = lines;
        }
        getOrderlines() {
            return this.lines;
        }
        getPartner() {
            return this.partner_id || null;
        }
    }
    const source = fs.readFileSync(new URL('overrides/pos_store.js', root), 'utf8')
        .replace(/^import .*;\r?\n/gm, '')
        .replace(/^export /gm, '');

    let partnerRuleMock = { type: 'none', discount: 0 };
    const context = {
        PosStore: MockPosStore,
        PosOrder: MockPosOrder,
        patch,
        getPartnerPricingRule: (models, partner, product, options) => partnerRuleMock,
        console,
    };
    vm.runInNewContext(source, context);
    return {
        MockPosOrder,
        setPartnerRule: (rule) => { partnerRuleMock = rule; },
    };
}

test('pos_store: reconciliacion propaga promocion de orden a nuevas lineas', () => {
    const { MockPosOrder, setPartnerRule } = createPosStoreContext();
    setPartnerRule({ type: 'none', discount: 0 });

    const orderPromo = {
        id: 77,
        program_id: { name: 'Promo 15% Orden' },
        discount: 15,
        discount_applicability: 'order',
    };

    const lineExisting = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 15,
        pricing_rule_type: 'promo',
        price_origin: 'promo',
        promo_reward_id: orderPromo,
        product_id: { id: 10 },
        setDiscount(d) { this.discount = d; },
    };

    const lineNew = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 0,
        pricing_rule_type: 'none',
        product_id: { id: 20 },
        setDiscount(d) { this.discount = d; },
    };

    const order = new MockPosOrder([lineExisting, lineNew]);
    order.order_promo_reward_id = orderPromo;
    order._isRewardValidForOrder = () => true;

    order._entintadosReconcileDiscounts();

    // La nueva linea recibe la promocion de orden de 15%
    assert.equal(lineNew.discount, 15);
    assert.equal(lineNew.pricing_rule_type, 'promo');
    assert.equal(lineNew.price_origin, 'promo');
    assert.equal(lineNew.promo_reward_id, orderPromo);
});

test('pos_store: reconciliacion revierte promocion de orden si deja de ser valida', () => {
    const { MockPosOrder, setPartnerRule } = createPosStoreContext();
    setPartnerRule({ type: 'discount', discount: 10, originType: 'partner_rule', originLabel: 'Acuerdo 10%' });

    const orderPromo = {
        id: 77,
        program_id: { name: 'Promo 15% Orden' },
        discount: 15,
        discount_applicability: 'order',
    };

    const line = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 15,
        pricing_rule_type: 'promo',
        price_origin: 'promo',
        promo_reward_id: orderPromo,
        product_id: { id: 10 },
        setDiscount(d) { this.discount = d; },
    };

    const order = new MockPosOrder([line]);
    order.order_promo_reward_id = orderPromo;
    // Simular que el pedido ya no cumple montos minimos
    order._isRewardValidForOrder = () => false;

    order._entintadosReconcileDiscounts();

    // La promo de orden se invalida
    assert.equal(order.order_promo_reward_id, false);
    // La partida revierte a su acuerdo comercial de 10%
    assert.equal(line.discount, 10);
    assert.equal(line.pricing_rule_type, 'partner_rule');
    assert.equal(line.price_origin, 'agreement');
    assert.equal(line.promo_reward_id, false);
});

test('pos_store: reconciliacion respeta exclusividad de acuerdo comercial superior a promo de orden', () => {
    const { MockPosOrder, setPartnerRule } = createPosStoreContext();
    // Cliente tiene acuerdo comercial de 20%, superior a la promo de orden de 15%
    setPartnerRule({ type: 'discount', discount: 20, originType: 'partner_rule', originLabel: 'Acuerdo VIP 20%' });

    const orderPromo = {
        id: 77,
        program_id: { name: 'Promo 15% Orden' },
        discount: 15,
        discount_applicability: 'order',
    };

    const line = {
        getQuantity: () => 1,
        price_unit: 100,
        discount: 0,
        pricing_rule_type: 'none',
        product_id: { id: 10 },
        setDiscount(d) { this.discount = d; },
    };

    const order = new MockPosOrder([line]);
    order.order_promo_reward_id = orderPromo;
    order._isRewardValidForOrder = () => true;

    order._entintadosReconcileDiscounts();

    // Como acuerdo 20% > promo 15%, la linea toma el acuerdo comercial
    assert.equal(line.discount, 20);
    assert.equal(line.pricing_rule_type, 'partner_rule');
    assert.equal(line.price_origin, 'agreement');
    assert.equal(line.promo_reward_id, false);
});



