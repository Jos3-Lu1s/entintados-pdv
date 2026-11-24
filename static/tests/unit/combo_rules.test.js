import { describe, expect, test } from "@odoo/hoot";
import {
    comboItemAgreementValues,
    comboItemPriceLabel,
    distributeComboPrice,
    isComboItem,
    isComboPackage,
    splitComboLines,
} from "@entintados_pdv/app/utils/combo_rules";

// Datos planos con la forma de los modelos del PDV: el combo de la SPEC 23 de Ventas,
// con referencias de 500, 150 y 50.

const rounding = { round: (value) => Math.round(value * 100) / 100 };

function combo(id, basePrice, values = {}) {
    return { id, name: `Combo ${id}`, base_price: basePrice, qty_free: 1, ...values };
}

function item(comboRecord, { qty = 1, extraPrice = 0, attributes = [] } = {}) {
    return {
        product_id: { id: 100 + comboRecord.id, type: "consu" },
        combo_item_id: { id: 200 + comboRecord.id, combo_id: comboRecord, extra_price: extraPrice },
        attribute_value_ids: attributes.map((price_extra) => ({ price_extra })),
        qty,
    };
}

function comboPackage(qty, items) {
    return {
        product_id: { id: 1, name: "Paquete Pintor", type: "combo" },
        qty,
        combo_line_ids: items,
    };
}

const PAINT = combo(1, 500, { name: "Pintura" });
const ROLLER = combo(2, 150, { name: "Rodillo" });
const BRUSH = combo(3, 50, { name: "Brocha" });

function distribute(packageLine, total) {
    const { freeLines, extraLines, packageQty } = splitComboLines(packageLine);
    const prices = distributeComboPrice({ total, packageQty, freeLines, extraLines, rounding });
    return packageLine.combo_line_ids.map((line) => prices.get(line));
}

describe("entintados_pdv: combo_rules", () => {
    test("reparte el precio de tarifa como el core", () => {
        const packageLine = comboPackage(1, [item(PAINT), item(ROLLER), item(BRUSH)]);
        expect(distribute(packageLine, 1000)).toEqual([714.29, 214.29, 71.42]);
    });

    test("reparte el precio fijo y el último absorbe el residuo", () => {
        const packageLine = comboPackage(1, [item(PAINT), item(ROLLER), item(BRUSH)]);
        const prices = distribute(packageLine, 200);
        expect(prices).toEqual([142.86, 42.86, 14.28]);
        expect(rounding.round(prices.reduce((sum, price) => sum + price, 0))).toBe(200);
    });

    test("con 3 paquetes el reparto por paquete no cambia", () => {
        const packageLine = comboPackage(3, [
            item(PAINT, { qty: 3 }),
            item(ROLLER, { qty: 3 }),
            item(BRUSH, { qty: 3 }),
        ]);
        expect(distribute(packageLine, 200)).toEqual([142.86, 42.86, 14.28]);
        const { freeLines, extraLines } = splitComboLines(packageLine);
        expect(freeLines.length).toBe(3);
        expect(extraLines.length).toBe(0);
    });

    test("un extra vale su base_price y los cargos extra se suman encima", () => {
        const extra = item(BRUSH, { extraPrice: 5 });
        const packageLine = comboPackage(1, [
            item(PAINT, { attributes: [10] }),
            item(ROLLER),
            item(BRUSH),
            extra,
        ]);
        const { freeLines, extraLines } = splitComboLines(packageLine);
        expect(extraLines).toEqual([extra]);
        expect(freeLines.length).toBe(3);
        expect(distribute(packageLine, 200)).toEqual([152.86, 42.86, 14.28, 55]);
    });

    test("el producto copia el acuerdo del paquete con el prefijo", () => {
        const rule = { id: 9 };
        expect(
            comboItemAgreementValues({
                discount: 15,
                pricing_rule_type: "product",
                pricing_rule_origin: "Producto · 15%",
                pricing_rule_id: rule,
            })
        ).toEqual({
            discount: 15,
            pricing_rule_type: "product",
            pricing_rule_origin: "↳ Producto · 15%",
            pricing_rule_id: rule,
        });
        expect(
            comboItemAgreementValues({ discount: 20, pricing_rule_type: "promo", pricing_rule_origin: "Esp · 20%" })
        ).toEqual({
            discount: 20,
            pricing_rule_type: "promo",
            pricing_rule_origin: "↳ Esp · 20%",
            pricing_rule_id: false,
        });
        expect(
            comboItemAgreementValues({
                discount: 0,
                pricing_rule_type: "fixed_price",
                pricing_rule_origin: "Precio fijo",
                pricing_rule_id: rule,
            })
        ).toEqual({
            discount: 0,
            pricing_rule_type: "fixed_price",
            pricing_rule_origin: "↳ Precio fijo",
            pricing_rule_id: rule,
        });
        expect(comboItemAgreementValues({ discount: 0 })).toEqual({
            discount: 0,
            pricing_rule_type: "none",
            pricing_rule_origin: "",
            pricing_rule_id: false,
        });
    });

    test("la leyenda del producto nombra el paquete y su combo", () => {
        const paint = item(PAINT);
        expect(comboItemPriceLabel(comboPackage(1, [paint]), paint)).toBe(
            "Parte del combo «Paquete Pintor»: Pintura"
        );
    });

    test("la base entintada y sus colorantes no son combo", () => {
        const colorant = { product_id: { id: 50, tint_role: "colorant" }, combo_parent_id: { id: 1 }, qty: 2 };
        const tintedBase = { product_id: { id: 51, type: "consu" }, combo_line_ids: [colorant], qty: 1 };
        expect(isComboPackage(tintedBase)).toBe(false);
        expect(isComboItem(colorant)).toBe(false);
        expect(isComboPackage(comboPackage(1, []))).toBe(true);
        expect(isComboItem(item(PAINT))).toBe(true);
    });
});
