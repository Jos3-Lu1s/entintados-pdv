import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { isTintColorant } from "@entintados_pdv/app/utils/tint_order";

patch(PosOrder.prototype, {
    /**
     * Valida si una línea de orden de venta debe ser excluida de la base descontable de promociones.
     */
    _isLineExcludedFromPromo(line, reward) {
        if (!line || !line.getQuantity() || !line.price_unit) {
            return true;
        }
        // 1. Precios fijos pactados quedan 100% excluidos
        if (line.fixed_price_locked || line.pricing_rule_type === "fixed_price") {
            return true;
        }
        // 2. Colorantes formulados de entintado quedan 100% excluidos
        if (isTintColorant(line) || line.is_tint_colorant || line.product_id?.tint_role === "colorant") {
            return true;
        }
        // 3. Componentes formulados de combos quedan 100% excluidos
        if (line.combo_parent_id) {
            return true;
        }
        // 4. Exclusión mutua con acuerdos comerciales de descuento:
        // Si el descuento del acuerdo comercial es mayor o igual al de la promoción, la línea queda excluida
        if (this._hasCommercialAgreementDiscount(line)) {
            const agreementDiscount = line.discount || 0;
            const promoDiscount = reward?.discount || 0;
            if (promoDiscount <= agreementDiscount) {
                return true;
            }
        }
        return false;
    },

    /**
     * Determina si la línea cuenta con un descuento activo por acuerdo comercial.
     */
    _hasCommercialAgreementDiscount(line) {
        return Boolean(
            line.pricing_rule_type &&
            line.pricing_rule_type !== "none" &&
            line.pricing_rule_type !== "fixed_price" &&
            line.pricing_rule_type !== "promo" &&
            line.discount > 0
        );
    },

    /**
     * Calcula el factor proporcional para no encimar descuentos:
     * Si la promoción (ej. 20%) supera al acuerdo comercial (ej. 15%),
     * solo bonifica la diferencia para alcanzar el beneficio total de la promoción.
     */
    _getPromoEffectiveLineFactor(line, reward) {
        if (this._hasCommercialAgreementDiscount(line)) {
            const agreementDiscount = line.discount || 0;
            const promoDiscount = reward?.discount || 0;
            if (promoDiscount > agreementDiscount && promoDiscount > 0) {
                return (promoDiscount - agreementDiscount) / promoDiscount;
            }
        }
        return 1.0;
    },

    /**
     * Sobrescribe el cálculo de base descontable para promociones específicas.
     */
    _getDiscountableOnSpecific(reward) {
        const applicableProductIds = new Set(reward.all_discount_product_ids?.map((p) => p.id) || []);
        const linesToDiscount = [];
        const discountLinesPerReward = {};
        const orderLines = this.getOrderlines();
        const orderProducts = orderLines.map((line) => line.product_id?.id);
        const remainingAmountPerLine = {};

        for (const line of orderLines) {
            if (!line.getQuantity() || !line.price_unit) {
                continue;
            }
            if (this._isLineExcludedFromPromo(line, reward)) {
                continue;
            }
            const factor = this._getPromoEffectiveLineFactor(line, reward);
            remainingAmountPerLine[line.uuid] = line.prices.total_included * factor;

            const product_id = line.combo_parent_id?.product_id?.id || line.getProduct()?.id;
            if (
                applicableProductIds.has(product_id) ||
                (line._reward_product_id && applicableProductIds.has(line._reward_product_id.id))
            ) {
                linesToDiscount.push(line);
            } else if (line.reward_id) {
                const lineReward = line.reward_id;
                const lineRewardApplicableProductsIds = new Set(
                    lineReward.all_discount_product_ids?.map((p) => p.id) || []
                );
                if (
                    lineReward.id === reward.id ||
                    (orderProducts.some(
                        (product) =>
                            lineRewardApplicableProductsIds.has(product) &&
                            applicableProductIds.has(product)
                    ) &&
                        lineReward.reward_type === "discount" &&
                        lineReward.discount_mode !== "percent")
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
            if (!discountedLines.length) {
                continue;
            }
            if (lineReward.discount_mode === "percent") {
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
        }

        let discountable = 0;
        const discountablePerTax = {};
        for (const line of linesToDiscount) {
            const lineRemaining = remainingAmountPerLine[line.uuid] || 0;
            discountable += lineRemaining;
            const taxKey = line.tax_ids.map((t) => t.id);
            if (!discountablePerTax[taxKey]) {
                discountablePerTax[taxKey] = 0;
            }
            const ratio = line.prices.total_included ? lineRemaining / line.prices.total_included : 0;
            discountablePerTax[taxKey] += line.basePrice * ratio;
        }
        return { discountable, discountablePerTax };
    },

    /**
     * Sobrescribe el cálculo de base descontable para promociones globales de orden completa.
     */
    _getDiscountableOnOrder(reward) {
        let discountable = 0;
        const discountablePerTax = {};
        for (const line of this.getOrderlines()) {
            if (!line.getQuantity()) {
                continue;
            }
            if (this._isLineExcludedFromPromo(line, reward)) {
                continue;
            }
            const factor = this._getPromoEffectiveLineFactor(line, reward);
            const lineTotal = line.prices.total_included * factor;
            const lineBase = line.basePrice * factor;

            const taxKey = ["ewallet", "gift_card"].includes(reward.program_id?.program_type)
                ? line.tax_ids.map((t) => t.id)
                : line.tax_ids.filter((t) => t.amount_type !== "fixed").map((t) => t.id);

            discountable += lineTotal;
            if (!discountablePerTax[taxKey]) {
                discountablePerTax[taxKey] = 0;
            }
            discountablePerTax[taxKey] += lineBase;
        }
        return { discountable, discountablePerTax };
    },

    /**
     * Valida y construye las líneas de recompensa para productos de regalo (100% descuento).
     * Impide que se seleccionen bases entintadas o colorantes como productos de regalo.
     */
    _getRewardLineValuesProduct(args) {
        const reward = args["reward"];
        const product =
            reward.reward_product_ids?.find((p) => p.id === args["product"]?.id) ||
            reward.reward_product_ids?.[0] ||
            reward.reward_product_id;

        if (product && (product.tint_role === "colorant" || product.tint_role === "base" || product.product_tmpl_id?.tint_role === "colorant" || product.product_tmpl_id?.tint_role === "base")) {
            return _t("Los componentes formulados no son elegibles como productos de regalo.");
        }

        const lines = super._getRewardLineValuesProduct(args);
        if (Array.isArray(lines)) {
            for (const line of lines) {
                line.price_origin = "promo";
                line.price_origin_label = reward?.program_id?.name || "Regalo de promoción";
                line.promo_reward_id = reward;
                line.pricing_rule_type = "promo";
            }
        }
        return lines;
    },

    /**
     * Asigna trazabilidad comercial al crear la línea de recompensa.
     */
    applyRewardLine(rewardLine) {
        const prepareRewards = {
            ...rewardLine,
            price_origin: "promo",
            price_origin_label: rewardLine.reward_id?.program_id?.name || "Promoción",
            promo_reward_id: rewardLine.reward_id,
            pricing_rule_type: "promo",
        };
        return super.applyRewardLine(prepareRewards);
    },
});

patch(ControlButtons.prototype, {
    async _applyReward(reward, coupon_id, potentialQty) {
        if (reward?.reward_type === "product" && reward.multi_product) {
            const originalProducts = reward.reward_product_ids || [];
            const nonFormulaProducts = originalProducts.filter(
                (p) => p.tint_role !== "colorant" && p.tint_role !== "base" &&
                       p.product_tmpl_id?.tint_role !== "colorant" && p.product_tmpl_id?.tint_role !== "base"
            );
            if (nonFormulaProducts.length === 0 && originalProducts.length > 0) {
                this.notification?.add(
                    _t("Los componentes formulados no son elegibles como productos de regalo."),
                    { type: "warning" }
                );
                return false;
            }
        }
        return super._applyReward(reward, coupon_id, potentialQty);
    },
});
