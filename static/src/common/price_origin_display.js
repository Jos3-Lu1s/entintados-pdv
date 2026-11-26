/**
 * Icono y color del origen del precio y del badge del acuerdo o la promoción, iguales en Ventas
 * y en la caja. Sin dependencias de Odoo: lo cargan los dos bundles.
 */

/** Uno por valor de `PRICE_ORIGINS` (`utils/pricing_selection.py`). */
export const PRICE_ORIGIN_ICONS = {
    list: { icon: "fa-tag", color: "text-muted" },
    pricelist: { icon: "fa-list-alt", color: "text-info" },
    fixed_price: { icon: "fa-lock", color: "text-success" },
    manual: { icon: "fa-pencil", color: "text-warning" },
    combo: { icon: "fa-cubes", color: "text-primary" },
    tint: { icon: "fa-tint", color: "text-info" },
};

const PRICING_RULE_DISPLAY = {
    fixed_price: { icon: "fa-lock", color: "text-success" },
    promo: { icon: "fa-star", color: "text-primary" },
    manual: { icon: "fa-pencil", color: "text-warning" },
};

const AGREEMENT_DISPLAY = { icon: "fa-tag", color: "text-info" };
const NO_RULE_DISPLAY = { icon: "fa-tag", color: "" };

/** Icono y color del badge por `pricing_rule_type`: los mismos colores que el badge de Ventas. */
export function pricingRuleDisplay(pricingRuleType) {
    if (!pricingRuleType || pricingRuleType === "none") {
        return { ...NO_RULE_DISPLAY };
    }
    return { ...(PRICING_RULE_DISPLAY[pricingRuleType] || AGREEMENT_DISPLAY) };
}
