/** @odoo-module **/

import { formatOriginBadge } from "@entintados_pdv/app/utils/origin_badge";

/** Leyenda del acuerdo de precio fijo, igual que `FIXED_PRICE_LABEL` de Ventas. */
export const FIXED_PRICE_LABEL = "Precio fijo";

function recordId(value) {
    return value?.id ?? value ?? false;
}

function pricingResult(type, originType, originLabel = "", { price = null, discount = 0, rule = null } = {}) {
    return { type, originType, originLabel, price, discount, rule };
}

function discountResult(originType, name, rule) {
    const discount = Number(rule.discount || 0);
    return pricingResult("discount", originType, formatOriginBadge(name, discount), { discount, rule });
}

function companyCurrency(models) {
    const company =
        models?.["res.company"]?.getFirst?.() || models?.["pos.config"]?.getFirst?.()?.company_id;
    return company?.currency_id || null;
}

function currencyRecord(models, currency) {
    if (!currency) {
        return null;
    }
    return typeof currency === "object" ? currency : models?.["res.currency"]?.get?.(currency) || null;
}

/** Convierte un importe de la moneda de la compañía a la del pedido con las tasas cargadas. */
function toOrderCurrency(models, amount, currency) {
    const from = currencyRecord(models, companyCurrency(models));
    const to = currencyRecord(models, currency);
    if (!from || !to || from.id === to.id) {
        return amount;
    }
    const fromRate = Number(from.rate || 0);
    const toRate = Number(to.rate || 0);
    if (fromRate <= 0 || toRate <= 0) {
        return amount;
    }
    return (amount / fromRate) * toRate;
}

/**
 * Acuerdo comercial del cliente para un producto, con la misma prioridad y las mismas
 * leyendas que `res.partner._get_partner_pricing_rule` de Ventas:
 *   1. precio fijo por producto;
 *   2. descuento por producto;
 *   3. descuento por la línea del producto;
 *   4. descuento por el esquema del producto;
 *   5. descuento global del cliente.
 *
 * La línea del PDV siempre está en la UdM del producto, así que el precio fijo no se
 * convierte de UdM; sí se convierte de la moneda de la compañía a la del pedido.
 *
 * @param {Object} models - Modelos de la caja
 * @param {Object} partner - Cliente del pedido
 * @param {Object} product - `product.product`
 * @param {{ currency?: Object|number }} options - Moneda del pedido
 * @returns {{ type: string, originType: string, originLabel: string, price: number|null,
 *            discount: number, rule: Object|null }}
 */
export function resolveAgreement(models, partner, product, { currency = null } = {}) {
    if (!partner || !product) {
        return pricingResult("none", "none");
    }
    const partnerId = recordId(partner);
    const productId = recordId(product);
    const partnerRules = (models?.["res.partner.discount.rule"]?.getAll?.() || []).filter(
        (rule) => recordId(rule.partner_id) === partnerId
    );
    const firstRule = (ruleType, appliedOn, field, targetId) =>
        partnerRules.find(
            (rule) =>
                rule.rule_type === ruleType &&
                rule.applied_on === appliedOn &&
                recordId(rule[field]) === targetId
        );

    const fixedRule = firstRule("fixed_price", "0_product", "product_id", productId);
    if (fixedRule) {
        const price = toOrderCurrency(models, Number(fixedRule.fixed_price || 0), currency);
        return pricingResult("fixed_price", "fixed_price", FIXED_PRICE_LABEL, { price, rule: fixedRule });
    }

    const productRule = firstRule("discount", "0_product", "product_id", productId);
    if (productRule) {
        return discountResult("product", "Producto", productRule);
    }

    const template = product.product_tmpl_id || product;
    const targets = [
        ["1_line", "line_id", template.lines_product_id, "line", "Línea"],
        ["2_scheme", "scheme_id", template.scheme_id, "scheme", "Esquema"],
    ];
    for (const [appliedOn, field, target, originType, prefix] of targets) {
        const targetId = recordId(target);
        const rule = targetId && firstRule("discount", appliedOn, field, targetId);
        if (rule) {
            const name = target?.name || rule[field]?.name || "";
            return discountResult(originType, `${prefix} ${name}`, rule);
        }
    }

    const globalDiscount = Number(partner.discount || 0) * 100;
    if (globalDiscount) {
        return pricingResult("discount", "global", formatOriginBadge("Cliente", globalDiscount), {
            discount: globalDiscount,
        });
    }

    return pricingResult("none", "none");
}
