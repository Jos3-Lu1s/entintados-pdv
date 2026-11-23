import { describe, expect, test } from "@odoo/hoot";
import { resolveAgreement } from "@entintados_pdv/app/utils/agreement_rules";

// Datos planos con la forma de los modelos del PDV: sin `setupPosEnv`, los modelos propios
// (acuerdos, líneas, esquemas) no tienen datos simulados.

const scheme = { id: 31, name: "Arquitectónico" };
const productLine = { id: 21, name: "Vinílica", scheme };
const product = {
    id: 11,
    product_tmpl_id: { id: 111, lines_product_id: productLine, scheme_id: scheme },
};

const partner = { id: 1, discount: 0.05 };
const otherPartner = { id: 2, discount: 0 };

function rule(id, values) {
    return { id, partner_id: partner, discount: 0, fixed_price: 0, ...values };
}

const fixedRule = rule(1, { rule_type: "fixed_price", applied_on: "0_product", product_id: product, fixed_price: 80 });
const productRule = rule(2, { rule_type: "discount", applied_on: "0_product", product_id: product, discount: 10 });
const lineRule = rule(3, { rule_type: "discount", applied_on: "1_line", line_id: productLine, discount: 12 });
const schemeRule = rule(4, { rule_type: "discount", applied_on: "2_scheme", scheme_id: scheme, discount: 15 });

function models(rules) {
    return { "res.partner.discount.rule": { getAll: () => rules } };
}

describe("entintados_pdv: agreement_rules", () => {
    test("prioridad: precio fijo, producto, línea, esquema y cliente", () => {
        const all = [schemeRule, lineRule, productRule, fixedRule];
        expect(resolveAgreement(models(all), partner, product).originType).toBe("fixed_price");
        expect(resolveAgreement(models(all.slice(0, 3)), partner, product).originType).toBe("product");
        expect(resolveAgreement(models(all.slice(0, 2)), partner, product).originType).toBe("line");
        expect(resolveAgreement(models(all.slice(0, 1)), partner, product).originType).toBe("scheme");
        expect(resolveAgreement(models([]), partner, product).originType).toBe("global");
    });

    test("leyendas iguales a las de Ventas", () => {
        const fixed = resolveAgreement(models([fixedRule]), partner, product);
        expect(fixed).toEqual({
            type: "fixed_price",
            originType: "fixed_price",
            originLabel: "Precio fijo",
            price: 80,
            discount: 0,
            rule: fixedRule,
        });
        expect(resolveAgreement(models([productRule]), partner, product).originLabel).toBe("Producto · 10%");
        expect(resolveAgreement(models([lineRule]), partner, product).originLabel).toBe("Línea Vinílica · 12%");
        expect(resolveAgreement(models([schemeRule]), partner, product).originLabel).toBe(
            "Esquema Arquitectónico · 15%"
        );
        const global = resolveAgreement(models([]), partner, product);
        expect(global.originLabel).toBe("Cliente · 5%");
        expect(global.discount).toBe(5);
    });

    test("sin cliente no hay acuerdo", () => {
        const result = resolveAgreement(models([productRule]), false, product);
        expect(result.type).toBe("none");
        expect(result.originType).toBe("none");
        expect(result.originLabel).toBe("");
        expect(result.discount).toBe(0);
    });

    test("las reglas de otro cliente no cuentan", () => {
        const foreign = [fixedRule, productRule, lineRule, schemeRule].map((r) => ({
            ...r,
            partner_id: otherPartner,
        }));
        expect(resolveAgreement(models(foreign), partner, product).originType).toBe("global");
        expect(resolveAgreement(models(foreign), { id: 3, discount: 0 }, product).type).toBe("none");
    });
});
