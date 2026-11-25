import { describe, expect, test } from "@odoo/hoot";
import {
    PRICE_ORIGIN_ICONS,
    pricingRuleDisplay,
} from "@entintados_pdv/common/price_origin_display";

describe("entintados_pdv: price_origin_display", () => {
    test("un icono por cada valor de la selección de Ventas", () => {
        expect(Object.keys(PRICE_ORIGIN_ICONS).sort()).toEqual(
            ["combo", "fixed_price", "list", "manual", "pricelist", "tint"]
        );
        expect(PRICE_ORIGIN_ICONS.combo).toEqual({ icon: "fa-cubes", color: "text-primary" });
    });

    test("precio fijo en verde", () => {
        expect(pricingRuleDisplay("fixed_price")).toEqual({ icon: "fa-lock", color: "text-success" });
    });

    test("promoción en morado", () => {
        expect(pricingRuleDisplay("promo")).toEqual({ icon: "fa-star", color: "text-primary" });
    });

    test("manual en naranja", () => {
        expect(pricingRuleDisplay("manual")).toEqual({ icon: "fa-pencil", color: "text-warning" });
    });

    test("acuerdos en azul", () => {
        for (const type of ["product", "line", "scheme", "global"]) {
            expect(pricingRuleDisplay(type)).toEqual({ icon: "fa-tag", color: "text-info" });
        }
    });

    test("sin acuerdo, sin color", () => {
        expect(pricingRuleDisplay("none")).toEqual({ icon: "fa-tag", color: "" });
        expect(pricingRuleDisplay(false)).toEqual({ icon: "fa-tag", color: "" });
        expect(pricingRuleDisplay(undefined)).toEqual({ icon: "fa-tag", color: "" });
    });
});
