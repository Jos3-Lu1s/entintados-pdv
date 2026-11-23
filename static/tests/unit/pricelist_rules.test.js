import { describe, expect, test } from "@odoo/hoot";
import { findPricelistRule, pricelistOriginLabel } from "@entintados_pdv/app/utils/pricelist_rules";

// Datos planos con la forma de los modelos del PDV: la tarifa trae sus reglas en `item_ids`.

const schemeS = { id: 1, name: "S" };
const schemeT = { id: 2, name: "T" };
const lineA = { id: 10, name: "A", scheme: schemeS };
const lineB = { id: 11, name: "B", scheme: schemeS };
const lineT = { id: 12, name: "T1", scheme: schemeT };
const category = { id: 5, name: "Pinturas", parent_id: false };

function product(id, productLine) {
    return {
        id,
        product_tmpl_id: {
            id: id * 10,
            lines_product_id: productLine,
            scheme_id: productLine.scheme,
            categ_id: category,
            parentCategories: [category.id],
        },
    };
}

const productA = product(1, lineA);
const productB = product(2, lineB);
const productT = product(3, lineT);

function pricelist(items) {
    const record = { id: 7, name: "Mayoreo" };
    record.item_ids = items.map((item) => ({ min_quantity: 0, pricelist_id: record, ...item }));
    return record;
}

function schemeItem(id, values = {}) {
    return { id, display_applied_on: "3_esquema", esquema_id: schemeS, linea_id: false, ...values };
}

describe("entintados_pdv: pricelist_rules", () => {
    test("una regla de esquema no aplica a un producto de otro esquema", () => {
        const list = pricelist([schemeItem(1)]);
        expect(findPricelistRule(list, productA, 1)?.id).toBe(1);
        expect(findPricelistRule(list, productT, 1)).toBe(null);
    });

    test("con línea, la regla de esquema solo aplica a esa línea", () => {
        const list = pricelist([schemeItem(1, { linea_id: lineA })]);
        expect(findPricelistRule(list, productA, 1)?.id).toBe(1);
        expect(findPricelistRule(list, productB, 1)).toBe(null);
    });

    test("min_quantity", () => {
        const list = pricelist([schemeItem(1, { min_quantity: 5 })]);
        expect(findPricelistRule(list, productA, 4)).toBe(null);
        expect(findPricelistRule(list, productA, 5)?.id).toBe(1);
    });

    test("la regla de variante gana sobre la de esquema", () => {
        const list = pricelist([schemeItem(1), { id: 2, product_id: productA, product_tmpl_id: productA.product_tmpl_id }]);
        expect(findPricelistRule(list, productA, 1)?.id).toBe(2);
        expect(findPricelistRule(list, productB, 1)?.id).toBe(1);
    });

    test("una regla de esquema con mayor min_quantity gana sobre una global", () => {
        const list = pricelist([{ id: 9 }, schemeItem(1, { min_quantity: 2 })]);
        expect(findPricelistRule(list, productA, 2)?.id).toBe(1);
        expect(findPricelistRule(list, productA, 1)?.id).toBe(9);
        expect(findPricelistRule(list, productT, 2)?.id).toBe(9);
    });

    test("leyenda del origen del precio de tarifa", () => {
        const list = pricelist([]);
        const item = { id: 1, name: "Esquema: S", price: "10% de descuento" };
        expect(pricelistOriginLabel(list, item)).toBe("Tarifa: Mayoreo — Esquema: S: 10% de descuento");
        expect(pricelistOriginLabel(list, null)).toBe("Precio de venta del producto");
    });
});
