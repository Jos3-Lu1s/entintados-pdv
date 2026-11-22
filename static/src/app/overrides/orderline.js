import { Component } from "@odoo/owl";
import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { formatCurrency } from "@web/core/currency";
import { usePopover } from "@web/core/popover/popover_hook";
import { tintedProductName } from "@entintados_pdv/app/utils/tint_order";

export const POS_PRICE_ORIGINS = {
    list: { icon: "fa-tag", color: "text-muted", title: _t("Precio de venta") },
    pricelist: { icon: "fa-list-alt", color: "text-info", title: _t("Tarifa") },
    agreement: { icon: "fa-lock", color: "text-success", title: _t("Acuerdo comercial") },
    fixed_price: { icon: "fa-lock", color: "text-success", title: _t("Precio fijo de acuerdo") },
    manual: { icon: "fa-pencil", color: "text-warning", title: _t("Manual") },
    promo: { icon: "fa-star", color: "text-primary", title: _t("Promoción") },
    combo: { icon: "fa-cubes", color: "text-primary", title: _t("Parte de combo") },
    tint: { icon: "fa-tint", color: "text-info", title: _t("Entintado") },
};

export function getPriceOriginData(line) {
    if (!line) {
        return null;
    }
    const originKey = line.price_origin || (line.fixed_price_locked ? "fixed_price" : "list");
    const config = POS_PRICE_ORIGINS[originKey] || POS_PRICE_ORIGINS.list;
    const detail = line.price_origin_label || (
        originKey === "list" ? _t("Precio de venta del producto") :
        originKey === "manual" ? _t("Modificado de forma manual") :
        originKey === "fixed_price" ? (line.pricing_rule_origin || _t("Precio fijo")) :
        originKey === "pricelist" ? (
            line.order_id?.pricelist_id
                ? formatPricelistOriginLabel(
                    line.order_id.pricelist_id,
                    getPricelistItemRule(line.order_id.pricelist_id, line.product_id, line.qty || 1, line.models),
                    line.product_id,
                    line.price_unit
                )
                : _t("Precio determinado por tarifa activa")
        ) :
        originKey === "tint" ? _t("Precio del entintado: base más colorantes") :
        originKey === "combo" ? _t("Parte de combo") :
        originKey === "promo" ? _t("Regalo de promoción") :
        line.pricing_rule_origin || config.title
    );
    return {
        key: originKey,
        icon: config.icon,
        color: config.color,
        title: config.title,
        detail,
    };
}

export class PosPriceOriginPopover extends Component {
    static template = "entintados_pdv.PosPriceOriginPopover";
    static props = {
        title: String,
        detail: { type: [String, Boolean], optional: true },
        close: { type: Function, optional: true },
    };
}

/**
 * Parche para el componente visual Orderline del POS para evitar la duplicación
 * de precio como anotación secundaria cuando la cantidad de la línea es 1,
 * e incorporar el indicador y popover de origen de precio.
 */
patch(Orderline.prototype, {
    setup() {
        super.setup(...arguments);
        try {
            this.priceOriginPopover = usePopover(PosPriceOriginPopover, { position: "left" });
        } catch {
            this.priceOriginPopover = null;
        }
    },

    onPriceOriginClick(ev, line) {
        ev.stopPropagation();
        if (!this.priceOriginPopover) {
            return;
        }
        if (this.priceOriginPopover.isOpen) {
            this.priceOriginPopover.close();
            return;
        }
        const origin = getPriceOriginData(line || this.line);
        if (!origin) {
            return;
        }
        this.priceOriginPopover.open(ev.currentTarget, {
            title: origin.title,
            detail: origin.detail,
        });
    },

    get lineScreenValues() {
        const values = super.lineScreenValues;
        const line = this.line;
        if (!line || !values) {
            return values;
        }

        // Si la cantidad es unitaria (1), se oculta la anotación redundante de precio unitario
        // para que solo aparezca el precio en la columna designada del POS.
        if (Math.abs(line.qty) === 1 && !this.props.basic_receipt && this.props.mode === "display") {
            values.displayPriceUnit = false;
        }

        if (!this.props.basic_receipt) {
            values.pricingRuleOrigin = line.pricing_rule_origin || null;
            values.pricingRuleType = line.pricing_rule_type || null;
            values.hasPricingRule = Boolean(line.pricing_rule_origin);
            values.hasPromo = Boolean(line.promo_reward_id);
            values.pricingRuleColor =
                values.pricingRuleType === "fixed_price" ? "text-success" :
                values.pricingRuleType === "promo" ? "text-primary" :
                values.pricingRuleType === "manual" ? "text-warning" :
                values.pricingRuleType && values.pricingRuleType !== "none" ? "text-info" :
                "";
            values.pricingRuleIcon =
                values.pricingRuleType === "fixed_price" ? "fa-lock" :
                values.pricingRuleType === "promo" ? "fa-star" :
                values.pricingRuleType === "manual" ? "fa-pencil" :
                "fa-tag";
        }

        if (this.props.mode === "display" && !this.props.basic_receipt) {
            const originData = getPriceOriginData(line);
            if (originData) {
                values.priceOriginIcon = originData.icon;
                values.priceOriginColor = originData.color;
                values.priceOriginTitle = originData.title;
                values.priceOriginDetail = originData.detail;
            }

            const components = [line, ...(line.combo_line_ids || [])];
            const priceExcl = components.reduce((total, component) => total + component.priceExcl, 0);
            const priceExclNoDiscount = components.reduce(
                (total, component) => total + component.priceExclNoDiscount, 0);
            if (values.price) {
                values.price = formatCurrency(priceExcl, line.currency.id);
            }
            values.noDiscountPrice = formatCurrency(priceExclNoDiscount, line.currency.id);
            if (values.displayPriceUnit && line.qty) {
                values.displayPriceUnit = `${formatCurrency(priceExclNoDiscount / line.qty, line.currency.id)} / ${
                    line.product_id?.uom_id?.name || ""
                }`;
            }
        }

        const tintName = tintedProductName(line);
        if (tintName) {
            values.name = tintName;
            return values;
        }

        if (this.props.mode === "display" && values.name) {
            const productCode = line.product_id?.default_code ||
                line.product_id?.product_tmpl_id?.default_code;
            const codes = [productCode].filter(Boolean);
            const prefix = codes.map((code) => `[${code}]`).join(" ");
            if (prefix && !values.name.startsWith(prefix + " ")) {
                // El nombre del core puede incluir ya la referencia del producto.
                const existingPrefix = productCode ? `[${productCode}] ` : "";
                const name = existingPrefix && values.name.startsWith(existingPrefix)
                    ? values.name.slice(existingPrefix.length) : values.name;
                values.name = `${prefix} ${name}`;
            }
        }

        return values;
    },
});
