import { describe, expect, test } from "@odoo/hoot";
import {
    comboBreakdown,
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

// Precios ya repartidos por la capa de precios, como los deja `_refreshComboPricing`.
function pricedPackage(prices, { qty = 1, discount = 0, origin = "" } = {}) {
    const items = [PAINT, ROLLER, BRUSH].map((comboRecord, index) => ({
        ...item(comboRecord, { qty }),
        price_unit: prices[index],
        discount,
        priceExcl: prices[index] * qty * (1 - discount / 100),
    }));
    return { ...comboPackage(qty, items), pricing_rule_origin: origin };
}

const money = (value) => `$ ${Number(value).toFixed(2)}`;
const number = (value) => (Number.isInteger(value) ? String(value) : value.toFixed(2));

function breakdown(packageLine, context = {}) {
    return comboBreakdown(packageLine, {
        pricelistPrice: 1000,
        agreement: null,
        packagePriceOrigin: "Precio de venta",
        rounding,
        formatMoney: money,
        formatNumber: number,
        ...context,
    });
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
    test("desglose del precio de tarifa: fórmulas, ajuste y totales", () => {
        const result = breakdown(pricedPackage([714.29, 214.29, 71.42]));
        expect(result.empty).toBe(false);
        expect(result.agreement).toBe(false);
        expect(result.pricelistPrice).toBe("$ 1000.00");
        expect(result.distributedPrice).toBe("$ 1000.00");
        expect(result.referenceSum).toBe("500 + 150 + 50 = 700");
        expect(result.groups.map((group) => group.formula)).toEqual([
            "500 / 700 × 1000 = 714.29",
            "150 / 700 × 1000 = 214.29",
            "50 / 700 × 1000 = 71.43",
        ]);
        expect(result.groups.map((group) => group.adjustment)).toEqual([false, false, "71.43 − 0.01 = 71.42"]);
        expect(result.groups.map((group) => group.unitPrice)).toEqual(["$ 714.29", "$ 214.29", "$ 71.42"]);
        expect(result.packageTotal).toBe("$ 1000.00");
        expect(result.quantity).toBe("1");
        expect(result.quantityTotal).toBe("$ 1000.00");
        expect(result.discountOrigin).toBe(false);
        expect(result.groups.every((group) => group.manualPrice === false)).toBe(true);
    });

    test("desglose con precio fijo: se reparte el acuerdo", () => {
        const result = breakdown(pricedPackage([142.86, 42.86, 14.28]), {
            agreement: { originLabel: "Precio fijo", price: 200 },
        });
        expect(result.agreement).toEqual({ label: "Precio fijo", price: "$ 200.00" });
        expect(result.distributedPrice).toBe("$ 200.00");
        expect(result.groups.map((group) => group.formula)).toEqual([
            "500 / 700 × 200 = 142.86",
            "150 / 700 × 200 = 42.86",
            "50 / 700 × 200 = 14.29",
        ]);
        expect(result.groups[2].adjustment).toBe("14.29 − 0.01 = 14.28");
        expect(result.packageTotal).toBe("$ 200.00");
    });

    test("la referencia del grupo es la opción más barata", () => {
        const options = [
            { product_id: { display_name: "Pintura Premium", lst_price: 800 } },
            { product_id: { display_name: "Pintura Económica", lst_price: 500 } },
        ];
        const packageLine = pricedPackage([714.29, 214.29, 71.42]);
        packageLine.combo_line_ids[0].combo_item_id.combo_id = { ...PAINT, combo_item_ids: options };
        expect(breakdown(packageLine).groups[0].referenceProduct).toBe("Pintura Económica");
    });

    test("descuento del paquete en cada producto", () => {
        const result = breakdown(pricedPackage([714.29, 214.29, 71.42], { discount: 15, origin: "Producto · 15%" }));
        expect(result.discountOrigin).toBe("Producto · 15%");
        expect(result.groups[0].discount).toBe("$ 714.29 − 15 % = $ 607.15");
        expect(result.packageTotalDiscounted).toBe("$ 850.00");
        expect(result.quantityTotal).toBe("$ 850.00");
    });

    test("con 3 paquetes el desglose es por paquete", () => {
        const result = breakdown(pricedPackage([714.29, 214.29, 71.42], { qty: 3 }));
        expect(result.groups[0].formula).toBe("500 / 700 × 1000 = 714.29");
        expect(result.packageTotal).toBe("$ 1000.00");
        expect(result.quantity).toBe("3");
        expect(result.quantityTotal).toBe("$ 3000.00");
    });

    test("sin productos no hay desglose", () => {
        expect(breakdown(comboPackage(1, []))).toEqual({ empty: true });
    });
});
