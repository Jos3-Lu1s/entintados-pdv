/** @odoo-module **/

/**
 * Resuelve la regla de precio fijo o descuento aplicable para un producto
 * en el POS conforme a la jerarquía acordada:
 *   1. Precio fijo por Producto (inviolable frente a promociones y descuentos)
 *   2. Descuento por Producto
 *   3. Descuento por Línea de producto
 *   4. Descuento por Esquema de producto
 *   5. Descuento Global del cliente (partner.discount)
 *   Fallback: sin descuento ni precio fijo especial.
 *
 * @param {Object} pos - Instancia del PosStore
 * @param {Object} partner - Registro del cliente actual
 * @param {Object} product - Registro de product.product
 * @returns {Object} { type: 'fixed_price'|'discount'|'none', price: number|null, discount: number, rule: Object|null }
 */
export function getPartnerPricingRule(pos, partner, product) {
    if (!partner || !product) {
        return { type: "none", originType: "none", originLabel: "", price: null, discount: 0, rule: null };
    }
    const partnerId = partner?.id ?? partner;
    const productId = product?.id ?? product;

    const models = pos?.models || pos?.data?.models || pos;

    // Obtener plantilla y IDs de línea y esquema
    const tmpl = product.raw?.product_tmpl_id || product.product_tmpl_id || product;
    const lineId =
        tmpl?.lines_product_id?.id ??
        tmpl?.lines_product_id ??
        product.lines_product_id?.id ??
        product.lines_product_id;
    const lineRecord =
        lineId && models?.["lines.product"]
            ? models["lines.product"].get(lineId)
            : null;
    const lineName = lineRecord?.name || tmpl?.lines_product_id?.name || product.lines_product_id?.name || "";

    const schemeId =
        lineRecord?.scheme?.id ??
        lineRecord?.scheme ??
        tmpl?.scheme_id?.id ??
        tmpl?.scheme_id ??
        product.scheme_id?.id ??
        product.scheme_id;
    const schemeRecord =
        schemeId && models?.["tint.schema"]
            ? models["tint.schema"].get(schemeId)
            : null;
    const schemeName = schemeRecord?.name || tmpl?.scheme_id?.name || product.scheme_id?.name || "";

    const allRules = models?.["res.partner.discount.rule"]?.getAll?.() || [];
    const partnerRules = allRules.filter((r) => {
        const pId = r.partner_id?.id ?? r.partner_id;
        return pId === partnerId;
    });

    // 1. Precio Fijo por Producto (Prioridad 1)
    const fixedRule = partnerRules.find(
        (r) =>
            r.rule_type === "fixed_price" &&
            r.applied_on === "0_product" &&
            (r.product_id?.id ?? r.product_id) === productId
    );
    if (fixedRule) {
        return {
            type: "fixed_price",
            originType: "fixed_price",
            originLabel: "Precio Fijo",
            price: Number(fixedRule.fixed_price || 0),
            discount: 0,
            rule: fixedRule,
        };
    }

    // 2. Descuento por Producto (Prioridad 2)
    const prodDiscountRule = partnerRules.find(
        (r) =>
            r.rule_type === "discount" &&
            r.applied_on === "0_product" &&
            (r.product_id?.id ?? r.product_id) === productId
    );
    if (prodDiscountRule) {
        const disc = Number(prodDiscountRule.discount || 0);
        return {
            type: "discount",
            originType: "product",
            originLabel: `Desc. Producto (${disc.toFixed(1)}%)`,
            price: null,
            discount: disc,
            rule: prodDiscountRule,
        };
    }

    // 3. Descuento por Línea de producto (Prioridad 3)
    if (lineId) {
        const lineRule = partnerRules.find(
            (r) =>
                r.rule_type === "discount" &&
                r.applied_on === "1_line" &&
                (r.line_id?.id ?? r.line_id) === lineId
        );
        if (lineRule) {
            const disc = Number(lineRule.discount || 0);
            const resolvedLineName = lineName || lineRule.line_id?.name || "";
            return {
                type: "discount",
                originType: "line",
                originLabel: `Desc. Línea: ${resolvedLineName} (${disc.toFixed(1)}%)`.trim(),
                price: null,
                discount: disc,
                rule: lineRule,
            };
        }
    }

    // 4. Descuento por Esquema (Prioridad 4)
    if (schemeId) {
        const schemeRule = partnerRules.find(
            (r) =>
                r.rule_type === "discount" &&
                r.applied_on === "2_scheme" &&
                (r.scheme_id?.id ?? r.scheme_id) === schemeId
        );
        if (schemeRule) {
            const disc = Number(schemeRule.discount || 0);
            const resolvedSchemeName = schemeName || schemeRule.scheme_id?.name || "";
            return {
                type: "discount",
                originType: "scheme",
                originLabel: `Desc. Esquema: ${resolvedSchemeName} (${disc.toFixed(1)}%)`.trim(),
                price: null,
                discount: disc,
                rule: schemeRule,
            };
        }
    }

    // 5. Descuento Global del Cliente (Prioridad 5)
    const globalDiscount = Number(partner.discount || 0) * 100;
    if (globalDiscount > 0) {
        return {
            type: "discount",
            originType: "global",
            originLabel: `Desc. Global Cliente (${globalDiscount.toFixed(1)}%)`,
            price: null,
            discount: globalDiscount,
            rule: null,
        };
    }

    return { type: "none", originType: "none", originLabel: "", price: null, discount: 0, rule: null };
}
