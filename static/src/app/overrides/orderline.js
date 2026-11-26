import { Component } from "@odoo/owl";
import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { patch } from "@web/core/utils/patch";
import { formatCurrency } from "@web/core/currency";
import { formatFloat } from "@web/core/utils/numbers";
import { usePopover } from "@web/core/popover/popover_hook";
import { tintedProductName } from "@entintados_pdv/app/utils/tint_order";
import { _t } from "@web/core/l10n/translation";
import { pricingRuleDisplay } from "@entintados_pdv/common/price_origin_display";
import { formatOriginBadge } from "@entintados_pdv/app/utils/origin_badge";
import { priceOriginData } from "@entintados_pdv/app/utils/price_origin";
import { comboBreakdown, isComboPackage } from "@entintados_pdv/app/utils/combo_rules";
import { agreementForLine } from "@entintados_pdv/app/utils/pricing_rules";
import { findPricelistRule } from "@entintados_pdv/app/utils/pricelist_rules";
import { PosComboBreakdownPopover } from "@entintados_pdv/app/components/combo_breakdown_popover/combo_breakdown_popover";

/** Cifras del desglose sin ceros sobrantes en los enteros: `500 / 700 × 1000 = 714.29`. */
function formatBreakdownNumber(value) {
    return Number.isInteger(value) ? String(value) : formatFloat(value, { digits: [16, 2] });
}

/** Desglose del paquete de un combo con lo que la función pura no puede leer sola. */
function packageBreakdown(line) {
    const order = line.order_id;
    const pricelist = order?.pricelist_id;
    const product = line.product_id;
    const rule = findPricelistRule(pricelist, product, 1);
    return comboBreakdown(line, {
        pricelistPrice: product.product_tmpl_id.getPrice(pricelist, 1, 0, false, product),
        // Solo un paquete en precio fijo reparte el precio del acuerdo.
        agreement: line.pricing_rule_type === "fixed_price" ? agreementForLine(order, line) : null,
        packagePriceOrigin: rule ? `Tarifa: ${pricelist.name}` : "Precio de venta",
        rounding: order?.currency,
        formatMoney: (amount) => formatCurrency(amount, line.currency.id),
        formatNumber: formatBreakdownNumber,
    });
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
            this.comboBreakdownPopover = usePopover(PosComboBreakdownPopover, { position: "left" });
        } catch {
            this.priceOriginPopover = null;
            this.comboBreakdownPopover = null;
        }
    },

    onPriceOriginClick(ev, line) {
        ev.stopPropagation();
        const target = line || this.line;
        const origin = priceOriginData(target);
        if (!origin) {
            return;
        }
        const breakdown = origin.key === "combo" && isComboPackage(target) ? packageBreakdown(target) : null;
        const [popover, props] = breakdown && !breakdown.empty
            ? [this.comboBreakdownPopover, { breakdown }]
            : [this.priceOriginPopover, { title: origin.title, detail: origin.detail }];
        if (!popover) {
            return;
        }
        if (popover.isOpen) {
            popover.close();
            return;
        }
        popover.open(ev.currentTarget, props);
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
            const ruleDisplay = pricingRuleDisplay(values.pricingRuleType);
            values.pricingRuleColor = ruleDisplay.color;
            values.pricingRuleIcon = ruleDisplay.icon;
            // Descuento sin badge: no se sabe de dónde viene, así que no se rotula como manual.
            values.discountBadge = line.discount ? formatOriginBadge(_t("Descuento"), line.discount) : null;
        }

        if (this.props.mode === "display" && !this.props.basic_receipt) {
            const originData = priceOriginData(line);
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
