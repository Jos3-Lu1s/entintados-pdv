import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { patch } from "@web/core/utils/patch";
import { formatCurrency } from "@web/core/currency";
import { tintedProductName } from "@entintados_pdv/app/utils/tint_order";

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
            const order = line.order_id;
            if (order && !order.finalized && !order.isRefund && line.qty > 0 && !line.is_reward_line &&
                !line.combo_parent_id && line.pricing_rule_type !== "fixed_price") {
                const applied = new Set(order.lines.map((item) => item.reward_id?.id));
                const promotions = (order.getClaimableRewards?.() || []).filter(({ reward, product }) => {
                    if (reward.program_id.program_type !== "promotion" || applied.has(reward.id)) {
                        return false;
                    }
                    if (reward.reward_type === "product") {
                        return product?.id === line.product_id.id;
                    }
                    return reward.reward_type === "discount" && reward.discount_applicability === "specific" &&
                        order._getSpecificDiscountableLines(reward).includes(line);
                });
                values.availablePromotions = [...new Set(promotions.map(({ reward }) => reward.description))]
                    .filter(Boolean).join(" · ");
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
