import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { patch } from "@web/core/utils/patch";
import { formatCurrency } from "@web/core/currency";

/**
 * Parche para el componente visual Orderline del POS para evitar la duplicación
 * de precio como anotación secundaria cuando la cantidad de la línea es 1.
 */
patch(Orderline.prototype, {
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
        }

        if (this.props.mode === "display" && !this.props.basic_receipt) {
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

        if (this.props.mode === "display" && values.name) {
            const productCode = line.product_id?.default_code ||
                line.product_id?.product_tmpl_id?.default_code;
            const codes = [...new Set([productCode, line.tint_color_code].filter(Boolean))];
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
