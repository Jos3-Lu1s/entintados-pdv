import { describe, expect, test } from "@odoo/hoot";
import { priceOriginData } from "@entintados_pdv/app/utils/price_origin";

// Datos planos con la forma de las líneas del PDV.

const productLine = { id: 10, name: "A" };
const product = {
    id: 1,
    type: "consu",
    product_tmpl_id: { id: 10, lines_product_id: productLine, scheme_id: false, categ_id: false },
};

function line(values = {}) {
    return { product_id: product, qty: 1, getQuantity: () => 1, ...values };
}

function data(values) {
    const origin = priceOriginData(line(values));
    return [origin.key, origin.icon, origin.color, origin.title, origin.detail];
}

describe("entintados_pdv: price_origin", () => {
    test("sin línea", () => {
        expect(priceOriginData(null)).toBe(null);
    });

    test("precio de venta", () => {
        expect(data({ price_origin: "list" })).toEqual([
            "list", "fa-tag", "text-muted", "Precio de venta", "Precio de venta del producto",
        ]);
        expect(data({ price_origin: "list", price_origin_label: "Otro" })[4]).toBe("Otro");
    });

    test("tarifa con leyenda, con tarifa sin regla y sin tarifa", () => {
        expect(data({ price_origin: "pricelist", price_origin_label: "Tarifa: X — R: 10" })).toEqual([
            "pricelist", "fa-list-alt", "text-info", "Tarifa", "Tarifa: X — R: 10",
        ]);
        const order = { pricelist_id: { id: 7, name: "Mayoreo", item_ids: [] } };
        expect(data({ price_origin: "pricelist", order_id: order })[4]).toBe(
            "Precio de venta del producto"
        );
        expect(data({ price_origin: "pricelist" })[4]).toBe("Precio determinado por tarifa activa");
    });

    test("precio fijo de acuerdo", () => {
        expect(data({ price_origin: "fixed_price" })).toEqual([
            "fixed_price", "fa-lock", "text-success", "Precio fijo de acuerdo", "Precio fijo",
        ]);
        expect(data({ price_origin: "fixed_price", pricing_rule_origin: "Precio fijo X" })[4]).toBe(
            "Precio fijo X"
        );
    });

    test("manual", () => {
        expect(data({ price_origin: "manual" })).toEqual([
            "manual", "fa-pencil", "text-warning", "Manual", "Modificado de forma manual",
        ]);
    });

    test("producto de un combo", () => {
        expect(
            data({ price_origin: "combo", price_origin_label: "Parte del combo «P»: Pinturas" })
        ).toEqual(["combo", "fa-cubes", "text-primary", "Parte de combo", "Parte del combo «P»: Pinturas"]);
    });

    test("entintado", () => {
        expect(data({ price_origin: "tint" })).toEqual([
            "tint", "fa-tint", "text-info", "Entintado", "Precio del entintado: base más colorantes",
        ]);
    });

    test("paquete de combo: abre el desglose", () => {
        const origin = priceOriginData(
            line({ price_origin: "pricelist", product_id: { ...product, type: "combo" } })
        );
        expect([origin.key, origin.icon, origin.title, origin.detail]).toEqual([
            "combo", "fa-cubes", "Desglose del combo", false,
        ]);
    });

    test("sin origen: precio fijo si está bloqueada, si no precio de venta", () => {
        expect(data({ fixed_price_locked: true })[0]).toBe("fixed_price");
        expect(data({})[0]).toBe("list");
    });

    test("valores retirados o desconocidos caen en precio de venta", () => {
        for (const value of ["agreement", "promo", "otro"]) {
            expect(data({ price_origin: value }).slice(0, 4)).toEqual([
                "list", "fa-tag", "text-muted", "Precio de venta",
            ]);
        }
    });
});
