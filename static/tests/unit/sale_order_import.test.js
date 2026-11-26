import { describe, expect, test } from "@odoo/hoot";
import {
    currentRulesResetValues,
    isImportedSaleLine,
    quotedTotal,
    snapshotDoses,
} from "@entintados_pdv/app/utils/sale_order_import";

// Datos planos con la forma de los modelos del PDV.

const colorantA = { id: 21, display_name: "Colorante A" };
const colorantB = { id: 22, display_name: "Colorante B" };

function models(products) {
    const byId = new Map(products.map((product) => [product.id, product]));
    return { "product.product": { get: (id) => byId.get(id) } };
}

describe("isImportedSaleLine", () => {
    test("línea de producto de Ventas", () => {
        expect(isImportedSaleLine({ sale_order_line_id: { id: 1, is_downpayment: false } })).toBe(true);
    });

    test("anticipo o deducción de Ventas", () => {
        expect(isImportedSaleLine({ sale_order_line_id: { id: 1, is_downpayment: true } })).toBe(false);
    });

    test("línea capturada en la caja o anticipo nativo sin línea de venta", () => {
        expect(isImportedSaleLine({ product_id: { id: 5 } })).toBe(false);
        expect(isImportedSaleLine({ sale_order_origin_id: { id: 42 } })).toBe(false);
        expect(isImportedSaleLine(null)).toBe(false);
    });
});

describe("currentRulesResetValues", () => {
    test("deja la línea sin precio manual, descuento, acuerdo ni promoción", () => {
        expect(currentRulesResetValues()).toEqual({
            price_type: "original",
            discount: 0,
            pricing_rule_type: "none",
            pricing_rule_origin: "",
            pricing_rule_id: false,
            promo_reward_id: false,
        });
    });

    test("cada llamada devuelve un objeto nuevo", () => {
        expect(currentRulesResetValues()).not.toBe(currentRulesResetValues());
    });
});

describe("snapshotDoses", () => {
    const snapshot = {
        doses: [
            { product_id: 21, points: 12, name: "Colorante A" },
            { product_id: 22, points: 0.5, name: "Colorante B" },
            { product_id: 23, points: 0, name: "Sin puntos" },
        ],
    };

    test("toma los puntos congelados con los productos de la caja", () => {
        const doses = snapshotDoses(models([colorantA, colorantB]), snapshot);
        expect(doses.map((dose) => [dose.colorant, dose.points])).toEqual([
            [colorantA, 12],
            [colorantB, 0.5],
        ]);
    });

    test("un colorante no cargado queda sin producto", () => {
        const doses = snapshotDoses(models([colorantA]), snapshot);
        expect(doses[1].colorant).toBe(null);
        expect(doses[1].colorantId).toBe(22);
    });

    test("sin fórmula no hay dosis", () => {
        expect(snapshotDoses(models([]), null)).toEqual([]);
        expect(snapshotDoses(models([]), { doses: [] })).toEqual([]);
    });
});

describe("quotedTotal", () => {
    test("prorratea el total de Ventas a la cantidad del ticket", () => {
        const lines = [
            { qty: 2, quotedSaleLine: { product_uom_qty: 4, price_total: 400 } },
            { qty: 1, quotedSaleLine: { product_uom_qty: 1, price_total: 116 } },
        ];
        expect(quotedTotal(lines)).toBe(316);
    });

    test("ignora líneas sin datos de Ventas o sin cantidad cotizada", () => {
        const lines = [
            { qty: 3 },
            { qty: 1, quotedSaleLine: { product_uom_qty: 0, price_total: 50 } },
            { qty: 1, quotedSaleLine: { product_uom_qty: 1, price_total: 80 } },
        ];
        expect(quotedTotal(lines)).toBe(80);
    });
});
