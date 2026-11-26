import { describe, expect, test } from "@odoo/hoot";
import {
    admitsLinePromotion,
    availablePromotionRewards,
    bestPromotionPlan,
    linePromotionInfo,
    lineRewards,
    programPoints,
    promotionLineCount,
} from "@entintados_pdv/app/utils/promotion_rules";

// Datos planos con la forma de los modelos del PDV: sin `setupPosEnv`, los modelos propios
// (acuerdos, líneas, esquemas) no tienen datos simulados.

function product(id, values = {}) {
    return { id, type: "consu", ...values };
}

function line(productRecord, { qty = 1, untaxed = 100, discount = 0, ...values } = {}) {
    const discounted = untaxed * (1 - discount / 100);
    return {
        product_id: productRecord,
        qty,
        discount,
        combo_line_ids: [],
        prices: { total_excluded: discounted, total_included: discounted },
        priceExclNoDiscount: untaxed,
        priceInclNoDiscount: untaxed,
        ...values,
    };
}

function program(id, { rules = [{}], rewards = [], ...values } = {}) {
    const record = {
        id,
        program_type: "promotion",
        trigger: "auto",
        active: true,
        pricelist_ids: [],
        ...values,
    };
    record.rule_ids = rules.map((rule, index) => ({
        id: id * 100 + index,
        mode: "auto",
        any_product: true,
        minimum_amount: 0,
        minimum_qty: 0,
        minimum_amount_tax_mode: "excl",
        reward_point_mode: "order",
        reward_point_amount: 1,
        ...rule,
    }));
    record.reward_ids = rewards.map((reward) => ({
        reward_type: "discount",
        discount_mode: "percent",
        discount_applicability: "order",
        discount_max_amount: 0,
        required_points: 1,
        all_discount_product_ids: [],
        ...reward,
        program_id: record,
    }));
    return record;
}

function order(lines, programs, values = {}) {
    const record = {
        lines,
        uiState: { codeActivatedProgramRules: [] },
        pricelist_id: false,
        order_promo_reward_id: false,
        getOrderlines: () => lines,
        getPartner: () => false,
        models: { "loyalty.program": { getAll: () => programs } },
        ...values,
    };
    for (const orderLine of lines) {
        orderLine.order_id = record;
    }
    return record;
}

describe("entintados_pdv: promotion_rules", () => {
    test("elegibilidad de la línea", () => {
        const regular = product(1);
        const colorant = product(2, { tint_role: "colorant" });
        const advance = product(3, { is_accounting_advance: true });
        const downPayment = product(4);
        const config = { advance_risk_enabled: true, down_payment_product_id: downPayment };

        const ineligible = {
            "sin producto": line(false),
            "cantidad 0": line(regular, { qty: 0 }),
            reembolso: line(regular, { refunded_orderline_id: 9 }),
            colorante: line(colorant),
            "producto de un combo": line(regular, { combo_parent_id: { id: 7 } }),
            "paquete de combo con precio fijo": line(product(5, { type: "combo" }), {
                pricing_rule_type: "fixed_price",
                fixed_price_locked: true,
            }),
            "recompensa nativa": line(regular, { is_reward_line: true }),
            "recompensa por reward_id": line(regular, { reward_id: { id: 1 } }),
            "precio fijo": line(regular, { pricing_rule_type: "fixed_price" }),
            "precio fijo bloqueado": line(regular, { fixed_price_locked: true }),
            "anticipo contable": line(advance),
            "aplicación de anticipo": line(regular, { advance_source_ref: 12 }),
            "anticipo nativo": line(downPayment, { order_id: { config_id: config } }),
        };
        for (const [label, orderLine] of Object.entries(ineligible)) {
            expect(admitsLinePromotion(orderLine)).toBe(false, { message: label });
        }

        const tintedBase = line(product(8, { tint_role: "base" }), {
            combo_line_ids: [line(colorant, { untaxed: 0 })],
        });
        expect(admitsLinePromotion(tintedBase)).toBe(true);
        expect(admitsLinePromotion(line(regular))).toBe(true);
        expect(admitsLinePromotion(line(product(5, { type: "combo" })))).toBe(true);
    });

    test("el dominio specific se evalúa contra el producto combo, no contra sus productos", () => {
        const paint = product(1);
        const comboProduct = product(5, { type: "combo" });
        const promo = program(1, {
            rewards: [
                { id: 1, discount: 20, discount_applicability: "specific", all_discount_product_ids: [paint] },
                { id: 2, discount: 15, discount_applicability: "specific", all_discount_product_ids: [comboProduct] },
            ],
        });
        const packageLine = line(comboProduct, { untaxed: 0 });
        const paintItem = line(paint, { combo_parent_id: packageLine, combo_item_id: { id: 3 } });
        packageLine.combo_line_ids = [paintItem];
        const rewards = lineRewards(order([packageLine, paintItem], [promo]));

        expect(rewards.get(packageLine).map((reward) => reward.id)).toEqual([2]);
        expect(rewards.get(paintItem)).toEqual([]);
    });

    test("el mínimo de un combo en promoción se mide sin el descuento de sus productos", () => {
        const promo = program(1, { rules: [{ minimum_amount: 950 }] });
        const reward = { id: 1, program_id: promo };
        const packageLine = line(product(5, { type: "combo" }), { untaxed: 0, promo_reward_id: reward });
        const items = [700, 200, 100].map((amount, index) =>
            line(product(10 + index), {
                untaxed: amount,
                discount: 10,
                combo_parent_id: packageLine,
                combo_item_id: { id: index + 1 },
            })
        );
        packageLine.combo_line_ids = items;
        expect(programPoints(order([packageLine, ...items], [promo]), promo)).toBe(1);

        packageLine.promo_reward_id = false;
        expect(programPoints(order([packageLine, ...items], [promo]), promo)).toBe(null);
    });

    test("una recompensa de línea no se ofrece al resto de su esquema", () => {
        const scheme = { id: 1 };
        const lineX = { id: 10, scheme };
        const inLineX = product(1, { lines_product_id: lineX, scheme_id: scheme });
        const otherLine = product(2, { lines_product_id: { id: 11, scheme }, scheme_id: scheme });
        const promo = program(1, {
            rewards: [{
                id: 1,
                discount: 20,
                discount_applicability: "specific",
                lines_product_id: lineX,
                scheme_id: scheme,
                all_discount_product_ids: [inLineX],
            }],
        });
        const first = line(inLineX);
        const second = line(otherLine);
        const rewards = lineRewards(order([first, second], [promo]));

        expect(rewards.get(first).map((reward) => reward.id)).toEqual([1]);
        expect(rewards.get(second)).toEqual([]);
    });

    test("el mínimo se mide sin la promoción propia, pero con el acuerdo", () => {
        const promo = program(1, { rules: [{ minimum_amount: 1000 }] });
        const reward = { id: 1, program_id: promo };

        const withPromotion = line(product(1), { untaxed: 1000, discount: 20, promo_reward_id: reward });
        expect(programPoints(order([withPromotion], [promo]), promo)).toBe(1);

        const withAgreement = line(product(1), { untaxed: 1000, discount: 20 });
        expect(programPoints(order([withAgreement], [promo]), promo)).toBe(null);
    });

    test("required_points se exige sin consumirse", () => {
        const promo = program(1, {
            rewards: [
                { id: 1, discount: 10, required_points: 1 },
                { id: 2, discount: 15, required_points: 1 },
                { id: 3, discount: 30, required_points: 2 },
            ],
        });
        const available = availablePromotionRewards(order([line(product(1))], [promo]));
        expect(available.map((reward) => reward.id)).toEqual([1, 2]);
    });

    test("el límite de usos no expulsa al pedido que ya usa el programa", () => {
        const promo = program(1, {
            limit_usage: true,
            max_usage: 5,
            total_order_count: 5,
            rewards: [{ id: 1, discount: 10 }],
        });
        expect(availablePromotionRewards(order([line(product(1))], [promo]))).toEqual([]);

        const applied = line(product(1), { promo_reward_id: promo.reward_ids[0] });
        expect(availablePromotionRewards(order([applied], [promo])).map((r) => r.id)).toEqual([1]);
    });

    test("con promoción de pedido activa solo se ofrecen promociones de pedido", () => {
        const regular = product(1);
        const promo = program(1, {
            rewards: [
                { id: 1, discount: 10, discount_applicability: "order" },
                { id: 2, discount: 20, discount_applicability: "specific", all_discount_product_ids: [regular] },
            ],
        });
        const orderLine = line(regular);
        const withOrderPromo = order([orderLine], [promo], { order_promo_reward_id: promo.reward_ids[0] });
        expect(lineRewards(withOrderPromo).get(orderLine).map((reward) => reward.id)).toEqual([1]);

        const withoutOrderPromo = order([orderLine], [promo]);
        expect(lineRewards(withoutOrderPromo).get(orderLine).map((reward) => reward.id)).toEqual([1, 2]);
    });

    test("mejores promociones: la de pedido de mayor %, con empate al menor id", () => {
        const promo = program(1, {
            rewards: [
                { id: 5, discount: 15 },
                { id: 4, discount: 15 },
                { id: 3, discount: 10 },
            ],
        });
        const plan = bestPromotionPlan(order([line(product(1))], [promo]));
        expect(plan.orderReward.id).toBe(4);
    });

    test("mejores promociones: la específica solo si supera el descuento actual", () => {
        const better = product(1);
        const equal = product(2);
        const promo = program(1, {
            rewards: [
                { id: 1, discount: 20, discount_applicability: "specific", all_discount_product_ids: [better] },
                { id: 2, discount: 10, discount_applicability: "specific", all_discount_product_ids: [equal] },
            ],
        });
        const improves = line(better, { discount: 10 });
        const same = line(equal, { discount: 10 });
        const plan = bestPromotionPlan(order([improves, same], [promo]));

        expect(plan.orderReward).toBe(undefined);
        expect(plan.lines.get(improves).id).toBe(1);
        expect(plan.lines.has(same)).toBe(false);
    });
    describe("información de la estrella y contador", () => {
        const currency = { round: (value) => Math.round(value * 100) / 100 };
        const P = product(1);

        // Programa con una `specific` de 20 % sobre P.
        function specificProgram(discount = 20) {
            return program(1, {
                name: "Promo TK",
                rewards: [{ id: 11, discount, discount_applicability: "specific", all_discount_product_ids: [P] }],
            });
        }

        const agreement10 = () => ({ type: "discount", discount: 10, originLabel: "Producto · 10%" });

        function info(orderRecord, orderLine, agreementFor = agreement10) {
            return linePromotionInfo(orderRecord, orderLine, { agreementFor });
        }

        test("sin recompensas no hay nada que ofrecer", () => {
            const orderLine = line(P);
            const orderRecord = order([orderLine], [], { currency });
            expect(info(orderRecord, orderLine)).toBe(false);
            expect(promotionLineCount(orderRecord)).toBe(0);
        });

        test("con 10 % de acuerdo, la de 20 % sobre 100 ahorra 10", () => {
            const orderLine = line(P, { discount: 10 });
            const orderRecord = order([orderLine], [specificProgram()], { currency });
            const result = info(orderRecord, orderLine);
            expect(result.fallback).toBe(false);
            expect(result.options).toEqual([
                {
                    reward_id: 11,
                    name: "Promo TK",
                    target: "",
                    discount: 20,
                    difference: 10,
                    applied: false,
                    scope: "specific",
                },
            ]);
            expect(promotionLineCount(orderRecord)).toBe(1);
        });

        test("con 30 % de acuerdo, la de 20 % da 10 menos", () => {
            const orderLine = line(P, { discount: 30 });
            const orderRecord = order([orderLine], [specificProgram()], { currency });
            expect(info(orderRecord, orderLine).options[0].difference).toBe(-10);
        });

        test("con la promoción aplicada: vuelta al acuerdo, al precio fijo o a nada", () => {
            const promo = specificProgram();
            const reward = promo.reward_ids[0];
            const orderLine = line(P, { discount: 20, promo_reward_id: reward, priceExcl: 80 });
            const orderRecord = order([orderLine], [promo], { currency });

            const withAgreement = info(orderRecord, orderLine);
            expect(withAgreement.options.map((option) => [option.reward_id, option.applied, option.difference]))
                .toEqual([[11, true, 0]]);
            expect(withAgreement.fallback).toEqual({ label: "Producto · 10%", difference: -10, scope: "specific" });

            const fixed = () => ({ type: "fixed_price", price: 70, originLabel: "Precio fijo" });
            expect(info(orderRecord, orderLine, fixed).fallback).toEqual({
                label: "Precio fijo",
                difference: 10,
                scope: "specific",
            });

            const none = () => ({ type: "none", originLabel: "" });
            expect(info(orderRecord, orderLine, none).fallback).toEqual({
                label: "sin acuerdo",
                difference: -20,
                scope: "specific",
            });
            // Su única promoción ya está aplicada: no cuenta.
            expect(promotionLineCount(orderRecord)).toBe(0);
        });

        test("la promoción de pedido se quita de todo el pedido", () => {
            const promo = program(2, { name: "Pedido", rewards: [{ id: 21, discount: 15 }] });
            const reward = promo.reward_ids[0];
            const orderLine = line(P, { discount: 15, promo_reward_id: reward });
            const orderRecord = order([orderLine], [promo], { currency, order_promo_reward_id: reward });
            const result = info(orderRecord, orderLine);
            expect(result.options[0].scope).toBe("order");
            expect(result.fallback.scope).toBe("order");
        });

        test("el producto de un combo informa la promoción de su paquete", () => {
            const promo = specificProgram();
            const reward = promo.reward_ids[0];
            const packageLine = line(product(5, { type: "combo" }), { discount: 20, promo_reward_id: reward });
            const comboItem = line(product(6), { combo_item_id: { id: 1 }, combo_parent_id: packageLine });
            const orderRecord = order([packageLine, comboItem], [promo], { currency });
            expect(info(orderRecord, comboItem)).toEqual({
                options: [
                    {
                        reward_id: 11,
                        name: "Promo TK",
                        target: "",
                        discount: 20,
                        difference: 0,
                        applied: true,
                        scope: "specific",
                    },
                ],
                fallback: false,
                from_package: true,
            });
            comboItem.combo_parent_id = line(product(5, { type: "combo" }));
            expect(info(orderRecord, comboItem)).toBe(false);
        });

        test("el paquete toma el importe de sus productos", () => {
            const COMBO = product(5, { type: "combo" });
            const promo = program(1, {
                name: "Esp",
                rewards: [{ id: 11, discount: 20, discount_applicability: "specific", all_discount_product_ids: [COMBO] }],
            });
            const items = [700, 300].map((untaxed, index) =>
                line(product(10 + index), { untaxed, combo_item_id: { id: index + 1 } })
            );
            const packageLine = line(COMBO, { untaxed: 0, combo_line_ids: items });
            for (const item of items) {
                item.combo_parent_id = packageLine;
            }
            const orderRecord = order([packageLine, ...items], [promo], { currency });
            const none = () => ({ type: "none", originLabel: "" });
            expect(info(orderRecord, packageLine, none).options[0].difference).toBe(200);
        });
    });
});
