import { describe, expect, test } from "@odoo/hoot";
import {
    admitsLinePromotion,
    availablePromotionRewards,
    bestPromotionPlan,
    lineRewards,
    programPoints,
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
            "paquete de combo": line(product(5, { type: "combo" })),
            "paquete con productos": line(regular, { combo_line_ids: [line(product(6))] }),
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
});
