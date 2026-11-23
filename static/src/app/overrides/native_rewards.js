import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { formatOriginBadge } from "@entintados_pdv/app/utils/origin_badge";
import { isAdvanceLine, isColorant } from "@entintados_pdv/app/utils/promotion_rules";

/**
 * Línea que las recompensas nativas en % (cupones, lealtad, códigos) no descuentan, como
 * `exclude_protected_discount_lines` de Ventas: precio fijo de acuerdo y colorantes, más los
 * productos de un combo y los anticipos del PDV.
 */
export function isProtectedFromNativeDiscount(line) {
    return Boolean(
        line.fixed_price_locked ||
            line.pricing_rule_type === "fixed_price" ||
            isColorant(line.product_id) ||
            line.combo_parent_id ||
            isAdvanceLine(line)
    );
}

function isFormulaComponent(product) {
    const role = product?.tint_role || product?.product_tmpl_id?.tint_role;
    return role === "colorant" || role === "base";
}

const GIFT_FORMULA_ERROR = () => _t("Los componentes formulados no son elegibles como productos de regalo.");

patch(PosOrder.prototype, {
    // Lógica del core con la exclusión de las líneas protegidas.
    _getDiscountableOnOrder(reward) {
        let discountable = 0;
        const discountablePerTax = {};
        for (const line of this.getOrderlines()) {
            if (!line.getQuantity() || isProtectedFromNativeDiscount(line)) {
                continue;
            }
            const taxKey = ["ewallet", "gift_card"].includes(reward.program_id.program_type)
                ? line.tax_ids.map((t) => t.id)
                : line.tax_ids.filter((t) => t.amount_type !== "fixed").map((t) => t.id);
            discountable += line.prices.total_included;
            if (!discountablePerTax[taxKey]) {
                discountablePerTax[taxKey] = 0;
            }
            discountablePerTax[taxKey] += line.basePrice;
        }
        return { discountable, discountablePerTax };
    },

    _getCheapestLine(reward) {
        const applicableProductIds = new Set(reward.all_discount_product_ids.map((p) => p.id));
        return this.getOrderlines()
            .filter(
                (line) =>
                    !line.reward_id &&
                    line.getQuantity() &&
                    !isProtectedFromNativeDiscount(line) &&
                    applicableProductIds.has(line.getProduct().id)
            )
            .toSorted(
                (lineA, lineB) => lineA.comboTotalPrice / lineA.qty - lineB.comboTotalPrice / lineB.qty
            )[0];
    },

    _getSpecificDiscountableLines(reward) {
        return super
            ._getSpecificDiscountableLines(reward)
            .filter((line) => !isProtectedFromNativeDiscount(line));
    },

    // Lógica del core con la exclusión de las líneas protegidas.
    _getDiscountableOnSpecific(reward) {
        const applicableProductIds = new Set(reward.all_discount_product_ids.map((p) => p.id));
        const linesToDiscount = [];
        const discountLinesPerReward = {};
        const orderLines = this.getOrderlines();
        const orderProducts = orderLines.map((line) => line.product_id.id);
        const remainingAmountPerLine = {};
        for (const line of orderLines) {
            if (!line.getQuantity() || !line.price_unit || isProtectedFromNativeDiscount(line)) {
                continue;
            }
            remainingAmountPerLine[line.uuid] = line.prices.total_included;
            const product_id = line.getProduct().id;
            if (
                applicableProductIds.has(product_id) ||
                (line._reward_product_id && applicableProductIds.has(line._reward_product_id.id))
            ) {
                linesToDiscount.push(line);
            } else if (line.reward_id) {
                const lineReward = line.reward_id;
                const lineRewardApplicableProductsIds = new Set(
                    lineReward.all_discount_product_ids.map((p) => p.id)
                );
                if (
                    lineReward.id === reward.id ||
                    (orderProducts.some(
                        (product) =>
                            lineRewardApplicableProductsIds.has(product) &&
                            applicableProductIds.has(product)
                    ) &&
                        lineReward.reward_type === "discount" &&
                        lineReward.discount_mode != "percent")
                ) {
                    linesToDiscount.push(line);
                }
                if (!discountLinesPerReward[line.reward_identifier_code]) {
                    discountLinesPerReward[line.reward_identifier_code] = [];
                }
                discountLinesPerReward[line.reward_identifier_code].push(line);
            }
        }

        let cheapestLine = false;
        for (const lines of Object.values(discountLinesPerReward)) {
            const lineReward = lines[0].reward_id;
            if (lineReward.reward_type !== "discount") {
                continue;
            }
            let discountedLines = orderLines;
            if (lineReward.discount_applicability === "cheapest") {
                cheapestLine = cheapestLine || this._getCheapestLine(lineReward);
                discountedLines = cheapestLine ? [cheapestLine] : [];
            } else if (lineReward.discount_applicability === "specific") {
                discountedLines = this._getSpecificDiscountableLines(lineReward);
            }
            if (!discountedLines.length || lineReward.discount_mode !== "percent") {
                continue;
            }
            const discount = lineReward.discount / 100;
            for (const line of discountedLines) {
                if (line.reward_id || remainingAmountPerLine[line.uuid] === undefined) {
                    continue;
                }
                if (lineReward.discount_applicability === "cheapest") {
                    remainingAmountPerLine[line.uuid] *= 1 - discount / line.getQuantity();
                } else {
                    remainingAmountPerLine[line.uuid] *= 1 - discount;
                }
            }
        }

        let discountable = 0;
        const discountablePerTax = {};
        for (const line of linesToDiscount) {
            discountable += remainingAmountPerLine[line.uuid];
            const taxKey = line.tax_ids.map((t) => t.id);
            if (!discountablePerTax[taxKey]) {
                discountablePerTax[taxKey] = 0;
            }
            discountablePerTax[taxKey] +=
                line.basePrice * (remainingAmountPerLine[line.uuid] / line.prices.total_included);
        }
        return { discountable, discountablePerTax };
    },

    /**
     * El regalo no puede ser una base entintada ni un colorante. El de una promoción lleva su
     * insignia, como `_get_reward_values_product` de Ventas.
     */
    _getRewardLineValuesProduct(args) {
        const reward = args["reward"];
        const product =
            reward.reward_product_ids?.find((p) => p.id === args["product"]?.id) ||
            reward.reward_product_ids?.[0] ||
            reward.reward_product_id;
        if (isFormulaComponent(product)) {
            return GIFT_FORMULA_ERROR();
        }
        const lines = super._getRewardLineValuesProduct(args);
        if (Array.isArray(lines) && reward.program_id?.program_type === "promotion") {
            for (const line of lines) {
                line.pricing_rule_type = "promo";
                line.pricing_rule_origin = formatOriginBadge(reward.program_id.name, _t("Regalo"));
            }
        }
        return lines;
    },
});

patch(ControlButtons.prototype, {
    /** Un regalo de varios productos debe ofrecer al menos uno que no sea de una fórmula. */
    async _applyReward(reward, coupon_id, potentialQty) {
        if (reward?.reward_type === "product" && reward.multi_product) {
            const products = reward.reward_product_ids || [];
            if (products.length && products.every(isFormulaComponent)) {
                this.notification.add(GIFT_FORMULA_ERROR(), { type: "warning" });
                return false;
            }
        }
        return super._applyReward(...arguments);
    },
});
