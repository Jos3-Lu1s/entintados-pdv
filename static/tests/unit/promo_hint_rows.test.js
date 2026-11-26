import { describe, expect, test } from "@odoo/hoot";
import { promoHintLines } from "@entintados_pdv/common/promo_hint_rows";

const identity = (text) => text;
const money = (amount) => `$ ${amount.toFixed(2)}`;

describe("entintados_pdv: promo_hint_rows", () => {
    test("describe ahorro, pérdida y empate contra el descuento actual", () => {
        const rows = promoHintLines(
            {
                options: [
                    { reward_id: 1, name: "Promo TK", discount: 20, difference: 30, applied: false },
                    { reward_id: 2, name: "Promo TK", discount: 5, difference: -15, applied: false },
                    { reward_id: 3, name: "Promo TK", discount: 10, difference: 0, applied: false },
                ],
                fallback: false,
            },
            identity,
            money
        );
        expect(rows.map((row) => [row.text, row.detail, row.rewardId])).toEqual([
            ["Promo TK - 20 %", "ahorra $ 30.00 más", 1],
            ["Promo TK - 5 %", "da $ 15.00 menos que el descuento actual", 2],
            ["Promo TK - 10 %", "da lo mismo que el descuento actual", 3],
        ]);
    });

    test("marca la aplicada y ofrece volver al acuerdo", () => {
        const rows = promoHintLines(
            {
                options: [
                    { reward_id: 1, name: "Promo TK", discount: 20, difference: 0, applied: true },
                    { reward_id: 2, name: "Promo TK", discount: 10, difference: -10, applied: false },
                ],
                fallback: { label: "Producto · 10%", difference: -10 },
            },
            identity,
            money
        );
        expect(rows[0].applied).toBe(true);
        expect(rows[0].detail).toBe("aplicada");
        expect(rows.at(-1)).toEqual({
            key: "fallback",
            text: "Sin promoción: vuelve a Producto · 10%",
            detail: "da $ 10.00 menos que el descuento actual",
            rewardId: false,
            applied: false,
        });
    });

    test("rotula las promociones de pedido y deja igual las de producto", () => {
        const rows = promoHintLines(
            {
                options: [
                    { reward_id: 1, name: "Promo Esp", discount: 20, difference: 20, applied: false, scope: "specific" },
                    { reward_id: 2, name: "Promo Pedido", discount: 10, difference: 10, applied: false, scope: "order" },
                ],
                fallback: false,
            },
            identity,
            money
        );
        expect(rows.map((row) => row.text)).toEqual([
            "Promo Esp - 20 %",
            "Promo Pedido - 10 % (todo el pedido)",
        ]);
    });

    test("ofrece quitar la promoción de pedido de todo el pedido", () => {
        const rows = promoHintLines(
            {
                options: [
                    { reward_id: 2, name: "Promo Pedido", discount: 10, difference: 0, applied: true, scope: "order" },
                ],
                fallback: { label: "Producto · 15%", difference: 5, scope: "order" },
            },
            identity,
            money
        );
        expect(rows.at(-1)).toEqual({
            key: "fallback",
            text: "Quitar la promoción del pedido",
            detail: "ahorra $ 5.00 más",
            rewardId: false,
            applied: false,
        });
    });

    test("describe la promoción que un producto del combo toma de su paquete", () => {
        const rows = promoHintLines(
            {
                options: [
                    { reward_id: 4, name: "Promo Esp", discount: 10, difference: 0, applied: true, scope: "specific" },
                ],
                fallback: false,
                from_package: true,
            },
            identity,
            money
        );
        expect(rows).toEqual([
            {
                key: "reward-4",
                text: "Promo Esp - 10 %",
                detail: "aplicada en el paquete",
                rewardId: 4,
                applied: true,
            },
        ]);
    });

    test("línea sin opciones", () => {
        expect(promoHintLines(false, identity, money)).toEqual([]);
        expect(promoHintLines({ options: [], fallback: false }, identity, money)).toEqual([]);
    });
});
