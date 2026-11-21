import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { isTintColorant } from "@entintados_pdv/app/utils/tint_order";
import { getPartnerPricingRule } from "@entintados_pdv/app/utils/pricing_rules";

patch(PosOrder, {
    extraFields: {
        ...(PosOrder.extraFields || {}),
        order_promo_reward_id: {
            model: "pos.order",
            name: "order_promo_reward_id",
            relation: "loyalty.reward",
            type: "many2one",
            local: true,
        },
    },
});

patch(PosOrderline, {
    extraFields: {
        ...(PosOrderline.extraFields || {}),
        promo_reward_id: {
            model: "pos.order.line",
            name: "promo_reward_id",
            relation: "loyalty.reward",
            type: "many2one",
            local: true,
        },
    },
});

/**
 * Identifica si una recompensa es una promoción porcentual aplicable
 * directamente sobre el campo de descuento de las partidas.
 */
export function isLinePromotionReward(reward) {
    return Boolean(
        reward &&
        reward.reward_type === "discount" &&
        reward.discount_mode === "percent" &&
        ["order", "specific"].includes(reward.discount_applicability) &&
        !reward.discount_max_amount &&
        ["promotion", "promo_code", "coupons"].includes(reward.program_id?.program_type)
    );
}

patch(PosOrder.prototype, {
    /**
     * Sobrescribe _getRewardLineValuesDiscount para que las promociones de porcentaje
     * no generen líneas de recompensa auxiliares negativas.
     */
    _getRewardLineValuesDiscount(args) {
        const reward = args?.["reward"] || args?.reward;
        if (isLinePromotionReward(reward)) {
            return [];
        }
        return super._getRewardLineValuesDiscount(...arguments);
    },

    /**
     * Sobrescribe _applyReward para aplicar promociones porcentuales directamente
     * a las partidas de la orden sin crear líneas de recompensa auxiliares.
     */
    _applyReward(reward, coupon_id, args) {
        if (isLinePromotionReward(reward)) {
            if (coupon_id && typeof this._getRealCouponPoints === "function") {
                const points = this._getRealCouponPoints(coupon_id);
                if (reward.required_points > 0 && points < reward.required_points) {
                    return _t("No hay suficientes puntos en el cupón para reclamar esta recompensa.");
                }
            }
            if (typeof this._applyLinePromotionDirect === "function") {
                const applied = this._applyLinePromotionDirect(reward);
                if (!applied && !this.getOrderlines().length) {
                    if (reward.discount_applicability === "order") {
                        this.order_promo_reward_id = reward;
                        return true;
                    }
                    return _t("No hay partidas elegibles para aplicar esta promoción.");
                }
                if (!applied && reward.discount_applicability === "order") {
                    this.order_promo_reward_id = reward;
                    return true;
                }
            }
            if (typeof this._entintadosReconcileDiscounts === "function") {
                this._entintadosReconcileDiscounts();
            }
            return true;
        }
        return super._applyReward(...arguments);
    },

    /**
     * Valida si una recompensa o programa promocional cumple las condiciones
     * mínimas del pedido (monto mínimo, cantidad mínima y vigencia).
     */
    _isRewardValidForOrder(reward) {
        if (!reward || !reward.program_id) {
            return false;
        }
        const program = reward.program_id;
        if (typeof this._programIsApplicable === "function") {
            if (!this._programIsApplicable(program)) {
                return false;
            }
        }
        const rules = program.rule_ids || [];
        if (!rules.length) {
            return true;
        }

        const lines = this.getOrderlines ? this.getOrderlines() : (this.lines || []);
        for (const rule of rules) {
            if (rule.mode === "with_code" && this.uiState?.codeActivatedProgramRules) {
                if (!this.uiState.codeActivatedProgramRules.includes(rule.id)) {
                    return false;
                }
            }
            let totalQty = 0;
            let amountWithTax = 0;
            let amountWithoutTax = 0;

            for (const line of lines) {
                if (line.combo_parent_id || line.refunded_orderline_id) {
                    continue;
                }
                const product = line.product_id;
                const productId = product?.id;
                const validIds = rule.validProductIds || new Set((rule.valid_product_ids || []).map((p) => p.id || p));
                const matches = rule.any_product || (productId && validIds.has(productId));

                if (matches) {
                    const qty = typeof line.getQuantity === "function" ? line.getQuantity() : (line.qty || 0);
                    totalQty += qty;
                    const priceUnit = line.price_unit || 0;
                    const grossLineIncl = (line.prices?.total_included !== undefined)
                        ? (line.priceInclNoDiscount || (line.discount ? (line.prices.total_included / (1 - line.discount / 100)) : line.prices.total_included))
                        : (priceUnit * qty);
                    const grossLineExcl = (line.prices?.total_excluded !== undefined)
                        ? (line.priceExclNoDiscount || (line.discount ? (line.prices.total_excluded / (1 - line.discount / 100)) : line.prices.total_excluded))
                        : (priceUnit * qty);

                    amountWithTax += grossLineIncl;
                    amountWithoutTax += grossLineExcl;
                }
            }

            if (rule.minimum_qty && totalQty < rule.minimum_qty) {
                return false;
            }
            const amountToCheck = rule.minimum_amount_tax_mode === "incl" ? amountWithTax : amountWithoutTax;
            if (rule.minimum_amount && amountToCheck < rule.minimum_amount) {
                return false;
            }
        }
        return true;
    },

    /**
     * Obtiene el descuento vigente por acuerdo comercial del cliente para la línea,
     * incluso si la partida tiene actualmente asignada una promoción.
     */
    _getLineCommercialAgreementDiscount(line) {
        if (this._hasCommercialAgreementDiscount(line)) {
            return line.discount || 0;
        }
        const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
        if (!partner || !line.product_id) {
            return 0;
        }
        const models = this.models || this.pos?.models;
        const rule = getPartnerPricingRule(models, partner, line.product_id);
        return rule?.type === "discount" ? Number(rule.discount || 0) : 0;
    },

    /**
     * Obtiene la información del acuerdo comercial vigente del cliente para la línea,
     * incluso si la partida tiene actualmente asignada una promoción.
     */
    _getLineCommercialAgreementInfo(line) {
        const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
        if (!partner || !line?.product_id) {
            return { discount: 0, name: "", originType: "none", rule: null };
        }
        const models = this.models || this.pos?.models;
        const rule = getPartnerPricingRule(models, partner, line.product_id);
        if (rule?.type === "discount") {
            return {
                discount: Number(rule.discount || 0),
                name: rule.name || "Acuerdo comercial",
                originType: rule.originType || "partner_rule",
                rule: rule,
            };
        }
        return { discount: 0, name: "", originType: "none", rule: null };
    },

    /**
     * Obtiene las promociones de porcentaje disponibles aplicables a una partida específica.
     */
    _getAvailablePromotionRewardsForLine(line) {
        if (!line || !line.product_id || line.fixed_price_locked || line.pricing_rule_type === "fixed_price") {
            return [];
        }
        if (isTintColorant(line) || line.is_tint_colorant || line.product_id?.tint_role === "colorant") {
            return [];
        }
        if (line.combo_parent_id) {
            return [];
        }

        const allAvailable = this._getAvailablePromotionRewards();
        const product = line.combo_parent_id?.product_id || line.product_id || line.getProduct?.();
        const productId = product?.id;
        const tmpl = product?.product_tmpl_id || product?.raw?.product_tmpl_id || product;
        const lineId = tmpl?.lines_product_id?.id ?? tmpl?.lines_product_id ?? product?.lines_product_id?.id ?? product?.lines_product_id;
        const schemeId = tmpl?.scheme_id?.id ?? tmpl?.scheme_id ?? product?.scheme_id?.id ?? product?.scheme_id;

        let matched = allAvailable.filter((reward) => {
            if (reward.discount_applicability === "order") {
                return true;
            }
            if (reward.discount_applicability === "specific") {
                const applicableProductIds = new Set((reward.all_discount_product_ids || []).map((p) => p.id || p));
                const rewardLineId = reward.lines_product_id?.id ?? reward.lines_product_id;
                const rewardSchemeId = reward.scheme_id?.id ?? reward.scheme_id;

                return applicableProductIds.has(productId) ||
                    (rewardLineId && lineId === rewardLineId) ||
                    (rewardSchemeId && schemeId === rewardSchemeId);
            }
            return false;
        });

        if (this.order_promo_reward_id) {
            matched = matched.filter((r) => r.discount_applicability === "order");
        }

        if (line.promo_reward_id && !matched.some((r) => r.id === line.promo_reward_id.id)) {
            matched.unshift(line.promo_reward_id);
        }

        return matched;
    },

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
        // 4. Anticipos contables quedan 100% excluidos
        if (line.product_id?.product_tmpl_id?.is_accounting_advance || line.advance_source_ref) {
            return true;
        }
        // Para promociones globales de orden completa, los acuerdos comerciales NO excluyen líneas
        if (reward?.discount_applicability === "order") {
            return false;
        }
        // 5. Exclusión mutua con acuerdos comerciales de descuento para promociones específicas:
        // Si el descuento del acuerdo comercial es mayor o igual al de la promoción, la línea queda excluida
        const agreementDiscount = this._getLineCommercialAgreementDiscount(line);
        const promoDiscount = reward?.discount || 0;
        if (agreementDiscount > 0 && promoDiscount <= agreementDiscount) {
            return true;
        }
        return false;
    },

    /**
     * Aplica la promoción porcentual directamente sobre el campo de descuento
     * de las partidas elegibles, registrando su procedencia comercial.
     */
    _applyLinePromotionDirect(reward) {
        const applicableProductIds = new Set(
            (reward.all_discount_product_ids || []).map((p) => p.id || p)
        );
        const rewardLineId = reward.lines_product_id?.id ?? reward.lines_product_id;
        const rewardSchemeId = reward.scheme_id?.id ?? reward.scheme_id;

        const isOrderPromo = reward.discount_applicability === "order";
        if (isOrderPromo) {
            this.order_promo_reward_id = reward;
            delete this.order_promo_opt_out;
        }

        const eligibleLines = this.getOrderlines().filter((line) => {
            if (this._isLineExcludedFromPromo(line, reward)) {
                return false;
            }
            if (reward.discount_applicability === "specific") {
                const product = line.combo_parent_id?.product_id || line.product_id || line.getProduct?.();
                const productId = product?.id;
                const tmpl = product?.product_tmpl_id || product?.raw?.product_tmpl_id || product;
                const lineId = tmpl?.lines_product_id?.id ?? tmpl?.lines_product_id ?? product?.lines_product_id?.id ?? product?.lines_product_id;
                const schemeId = tmpl?.scheme_id?.id ?? tmpl?.scheme_id ?? product?.scheme_id?.id ?? product?.scheme_id;

                const isMatch = applicableProductIds.has(productId) ||
                    (rewardLineId && lineId === rewardLineId) ||
                    (rewardSchemeId && schemeId === rewardSchemeId);
                return isMatch;
            }
            return true;
        });

        let changed = false;
        const targetLabel = reward.reward_target_label || (
            reward.lines_product_id?.name ? `Línea: ${reward.lines_product_id.name}` :
            reward.scheme_id?.name ? `Esquema: ${reward.scheme_id.name}` : ""
        );
        const targetPart = targetLabel ? ` · ${targetLabel}` : "";
        const promoOrigin = `${reward.program_id?.name || "Promoción"}${targetPart} - ${reward.discount}%`;

        for (const line of eligibleLines) {
            if (!isOrderPromo) {
                const agreementDiscount = this._getLineCommercialAgreementDiscount(line);
                if (agreementDiscount > 0 && agreementDiscount > reward.discount) {
                    continue;
                }
            }
            line._entintadosSettingDiscount = true;
            try {
                if (typeof line.setDiscount === "function") {
                    line.setDiscount(reward.discount);
                } else if (typeof line.set_discount === "function") {
                    line.set_discount(reward.discount);
                } else {
                    line.discount = reward.discount;
                }
                line.pricing_rule_type = "promo";
                line.pricing_rule_origin = promoOrigin;
                line.price_origin = "promo";
                line.price_origin_label = reward.program_id?.name || "Promoción";
                line.promo_reward_id = reward;
                delete line.promo_opt_out;
                changed = true;
            } finally {
                delete line._entintadosSettingDiscount;
            }
        }

        return changed;
    },

    /**
     * Aplica la promoción porcentual directamente sobre una partida individual,
     * reemplazando de forma excluyente cualquier acuerdo comercial previo.
     */
    _applySingleLinePromotion(line, reward) {
        if (!line || !line.getQuantity() || !line.price_unit) {
            return false;
        }
        if (line.fixed_price_locked || line.pricing_rule_type === "fixed_price") {
            return false;
        }
        if (isTintColorant(line) || line.is_tint_colorant || line.product_id?.tint_role === "colorant") {
            return false;
        }
        if (line.combo_parent_id) {
            return false;
        }

        const targetLabel = reward.reward_target_label || (
            reward.lines_product_id?.name ? `Línea: ${reward.lines_product_id.name}` :
            reward.scheme_id?.name ? `Esquema: ${reward.scheme_id.name}` : ""
        );
        const targetPart = targetLabel ? ` · ${targetLabel}` : "";
        const promoOrigin = `${reward.program_id?.name || "Promoción"}${targetPart} - ${reward.discount}%`;

        line._entintadosSettingDiscount = true;
        try {
            if (typeof line.setDiscount === "function") {
                line.setDiscount(reward.discount);
            } else if (typeof line.set_discount === "function") {
                line.set_discount(reward.discount);
            } else {
                line.discount = reward.discount;
            }
            line.pricing_rule_type = "promo";
            line.pricing_rule_origin = promoOrigin;
            line.price_origin = "promo";
            line.price_origin_label = reward.program_id?.name || "Promoción";
            line.promo_reward_id = reward;
        } finally {
            delete line._entintadosSettingDiscount;
        }
        return true;
    },

    /**
     * Obtiene las promociones de porcentaje disponibles cuyos programas cumplen
     * condiciones en el pedido actual.
     */
    _getAvailablePromotionRewards() {
        const promoRewards = [];
        const claimable = typeof this.getClaimableRewards === "function" ? this.getClaimableRewards() : [];
        for (const item of claimable) {
            const reward = item?.reward;
            if (isLinePromotionReward(reward) && !promoRewards.some((r) => r.id === reward.id)) {
                promoRewards.push(reward);
            }
        }

        const programs = this.models?.["loyalty.program"]?.getAll?.() || [];
        for (const program of programs) {
            if (!["promotion", "promo_code", "coupons"].includes(program.program_type)) {
                continue;
            }
            for (const reward of program.reward_ids || []) {
                if (isLinePromotionReward(reward) && !promoRewards.some((r) => r.id === reward.id)) {
                    if (typeof this._isRewardValidForOrder === "function") {
                        if (this._isRewardValidForOrder(reward)) {
                            promoRewards.push(reward);
                        }
                    } else {
                        promoRewards.push(reward);
                    }
                }
            }
        }
        return promoRewards;
    },

    /**
     * Aplica en bloque las mejores promociones disponibles a las líneas elegibles.
     * Retorna la cantidad de partidas beneficiadas.
     */
    _applyBestLinePromotions() {
        const available = this._getAvailablePromotionRewards();
        if (!available.length) {
            return 0;
        }

        let totalBenefited = 0;
        // 1. Promociones de orden completa: buscar la de mayor %
        const orderPromos = available
            .filter((r) => r.discount_applicability === "order")
            .sort((a, b) => (b.discount || 0) - (a.discount || 0));

        if (orderPromos.length > 0) {
            const bestOrderPromo = orderPromos[0];
            const applied = this._applyLinePromotionDirect(bestOrderPromo);
            if (applied) {
                totalBenefited += this.getOrderlines().filter(
                    (l) => l.promo_reward_id?.id === bestOrderPromo.id
                ).length;
                return totalBenefited;
            }
        }

        // 2. Promociones específicas por línea: evaluar por mayor porcentaje
        const specificPromos = available
            .filter((r) => r.discount_applicability === "specific")
            .sort((a, b) => (b.discount || 0) - (a.discount || 0));

        for (const promo of specificPromos) {
            if (this._applyLinePromotionDirect(promo)) {
                totalBenefited++;
            }
        }
        return totalBenefited;
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

    /**
     * Retira la promoción de una partida y restaura su acuerdo comercial vigente.
     * Si la partida tiene aplicada una promoción global de pedido, se retira de toda la orden.
     */
    _dropLinePromotion(line) {
        if (!line || !line.promo_reward_id) {
            return;
        }

        const isOrderPromo = line.promo_reward_id.discount_applicability === "order" ||
            (this.order_promo_reward_id && line.promo_reward_id.id === this.order_promo_reward_id.id);

        if (isOrderPromo) {
            this._dropAllLinePromotions();
            return;
        }

        line.promo_reward_id = false;
        line.promo_opt_out = true;
        line.pricing_rule_type = "none";
        line.pricing_rule_origin = "";
        line.pricing_rule_id = false;
        line.price_origin = "list";
        line.price_origin_label = "";

        if (this.order_promo_reward_id && !this.getOrderlines().some((l) => l.promo_reward_id?.id === this.order_promo_reward_id.id)) {
            this.order_promo_reward_id = false;
        }

        const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
        const models = this.models || this.pos?.models;
        const rule = (partner && models && line.product_id) ? getPartnerPricingRule(models, partner, line.product_id) : null;
        const agreementDiscount = rule?.type === "discount" ? Number(rule.discount || 0) : 0;

        line._entintadosSettingDiscount = true;
        try {
            if (typeof line.setDiscount === "function") {
                line.setDiscount(agreementDiscount);
            } else if (typeof line.set_discount === "function") {
                line.set_discount(agreementDiscount);
            } else {
                line.discount = agreementDiscount;
            }
            if (agreementDiscount > 0 && rule) {
                line.pricing_rule_type = rule.originType || "product";
                line.pricing_rule_origin = rule.name || rule.originLabel || "";
                line.pricing_rule_id = rule.rule?.id || false;
                line.price_origin = "agreement";
                line.price_origin_label = rule.name || rule.originLabel || "";
            } else {
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
                line.price_origin = (this.pricelist || this.config?.pricelist_id) ? "pricelist" : "list";
                line.price_origin_label = "";
            }
        } finally {
            delete line._entintadosSettingDiscount;
        }
    },

    /**
     * Retira las promociones de todas las partidas del pedido y restaura sus acuerdos comerciales.
     */
    _dropAllLinePromotions() {
        this.order_promo_reward_id = false;
        this.order_promo_opt_out = true;
        for (const line of this.getOrderlines()) {
            delete line.promo_opt_out;
            if (line.promo_reward_id) {
                line.promo_reward_id = false;
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
                line.price_origin = "list";
                line.price_origin_label = "";

                const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
                const models = this.models || this.pos?.models;
                const rule = (partner && models && line.product_id) ? getPartnerPricingRule(models, partner, line.product_id) : null;
                const agreementDiscount = rule?.type === "discount" ? Number(rule.discount || 0) : 0;

                line._entintadosSettingDiscount = true;
                try {
                    if (typeof line.setDiscount === "function") {
                        line.setDiscount(agreementDiscount);
                    } else if (typeof line.set_discount === "function") {
                        line.set_discount(agreementDiscount);
                    } else {
                        line.discount = agreementDiscount;
                    }
                    if (agreementDiscount > 0 && rule) {
                        line.pricing_rule_type = rule.originType || "product";
                        line.pricing_rule_origin = rule.name || rule.originLabel || "";
                        line.pricing_rule_id = rule.rule?.id || false;
                        line.price_origin = "agreement";
                        line.price_origin_label = rule.name || rule.originLabel || "";
                    } else {
                        line.pricing_rule_type = "none";
                        line.pricing_rule_origin = "";
                        line.pricing_rule_id = false;
                        line.price_origin = (this.pricelist || this.config?.pricelist_id) ? "pricelist" : "list";
                        line.price_origin_label = "";
                    }
                } finally {
                    delete line._entintadosSettingDiscount;
                }
            }
        }
    },

    _removeOrderPromotion() {
        this._dropAllLinePromotions();
    },
});

patch(ControlButtons.prototype, {
    async clickRewards() {
        const order = this.pos.getOrder();
        const selectedLine = order?.getSelectedOrderline();
        const rewards = (typeof this.getPotentialRewards === "function" ? this.getPotentialRewards() : []) || [];
        const availableFromOrder = (order && typeof order._getAvailablePromotionRewards === "function")
            ? order._getAvailablePromotionRewards()
            : [];

        for (const reward of availableFromOrder) {
            if (!rewards.some((item) => (item.reward?.id || item.id) === reward.id)) {
                rewards.push({ reward: reward, coupon_id: false });
            }
        }

        const rewardsList = rewards.map((item) => {
            const reward = item.reward || item;
            const programName = reward.program_id?.name || "";
            const targetLabel = reward.reward_target_label || (
                reward.lines_product_id?.name ? `Línea: ${reward.lines_product_id.name}` :
                reward.scheme_id?.name ? `Esquema: ${reward.scheme_id.name}` : ""
            );
            const targetPart = targetLabel ? ` · ${targetLabel}` : "";
            const discount = reward.discount ? ` - ${reward.discount}%` : "";
            const isApplied = selectedLine?.promo_reward_id?.id === reward.id ||
                (reward.discount_applicability === "order" && order?.order_promo_reward_id?.id === reward.id);
            const typeLabel = reward.discount_applicability === "order" ? ` [Orden]` : "";
            const label = `${programName}${typeLabel}${targetPart}${discount}${isApplied ? " ✓" : ""}`;
            return {
                id: reward.id,
                label: label || programName,
                description: `Add "${reward.description}"`,
                item: item,
            };
        });

        if (order?.order_promo_reward_id) {
            rewardsList.unshift({
                id: "remove_order_promo",
                label: _t("Sin promoción: quitar promoción de la orden"),
                description: _t("Vuelve todas las líneas a sus acuerdos vigentes"),
                item: { isRemoveAction: true, allLines: true },
                isDanger: true,
            });
        } else if (selectedLine?.promo_reward_id) {
            const agreementDiscount = order._getLineCommercialAgreementDiscount(selectedLine);
            const fallbackText = agreementDiscount > 0
                ? `${agreementDiscount}% de acuerdo comercial`
                : _t("precio de lista");
            rewardsList.unshift({
                id: "remove_selected_line_promo",
                label: _t("Sin promoción: vuelve a %s", fallbackText),
                description: _t("Quitar promoción de la línea seleccionada"),
                item: { isRemoveAction: true, line: selectedLine },
                isDanger: true,
            });
        } else if (order?.lines?.some((l) => l.promo_reward_id)) {
            rewardsList.push({
                id: "remove_all_line_promos",
                label: _t("Sin promoción: quitar promociones del pedido"),
                description: _t("Vuelve todas las líneas a sus acuerdos vigentes"),
                item: { isRemoveAction: true, allLines: true },
                isDanger: true,
            });
        }

        if (rewardsList.length >= 1) {
            this.dialog.add(SelectionPopup, {
                title: _t("Available rewards"),
                list: rewardsList,
                getPayload: (selected) => {
                    if (!selected) {
                        return;
                    }
                    if (selected.isRemoveAction) {
                        if (selected.allLines || selected.line?.promo_reward_id?.discount_applicability === "order" || order.order_promo_reward_id) {
                            order._dropAllLinePromotions();
                        } else if (selected.line) {
                            order._dropLinePromotion(selected.line);
                        } else {
                            order._dropAllLinePromotions();
                        }
                        if (typeof order._entintadosReconcileDiscounts === "function") {
                            order._entintadosReconcileDiscounts();
                        }
                        this.pos?.updateRewards?.();
                        return;
                    }
                    const reward = selected.reward || selected.item?.reward;
                    const coupon_id = selected.coupon_id || selected.item?.coupon_id;
                    const potentialQty = selected.potentialQty || selected.item?.potentialQty;

                    if (reward) {
                        delete order.order_promo_opt_out;
                        if (isLinePromotionReward(reward)) {
                            if (reward.discount_applicability === "order") {
                                order._applyLinePromotionDirect(reward);
                            } else if (selectedLine && !order._isLineExcludedFromPromo(selectedLine, reward)) {
                                order._applySingleLinePromotion(selectedLine, reward);
                            } else {
                                order._applyLinePromotionDirect(reward);
                            }
                            if (typeof order._entintadosReconcileDiscounts === "function") {
                                order._entintadosReconcileDiscounts();
                            }
                            this.pos?.updateRewards?.();
                        } else {
                            this._applyReward(
                                reward,
                                coupon_id,
                                potentialQty
                            );
                        }
                    }
                },
            });
        }
    },

    async _applyReward(reward, coupon_id, potentialQty) {
        if (!reward) {
            return false;
        }
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

patch(Orderline.prototype, {
    canHavePromotion(line) {
        if (!line || !line.product_id) return false;
        if (line.fixed_price_locked || line.pricing_rule_type === "fixed_price") return false;
        if (isTintColorant(line) || line.is_tint_colorant || line.product_id?.tint_role === "colorant") return false;
        if (line.combo_parent_id) return false;
        return true;
    },

    getPromotionButtonTitle(line) {
        if (line.promo_reward_id) {
            return _t("Promoción aplicada: %s (%s%%). Clic para cambiar o quitar.", line.price_origin_label || line.pricing_rule_origin || "", line.discount || 0);
        }
        return _t("Ver y asignar promociones disponibles para esta partida.");
    },

    async onSelectLinePromotion(line) {
        const order = line?.order_id || line?.order || this.env?.services?.pos?.getOrder?.();
        if (!order) return;

        const availableRewards = order._getAvailablePromotionRewardsForLine ? order._getAvailablePromotionRewardsForLine(line) : [];
        const partnerAgreement = order._getLineCommercialAgreementInfo ? order._getLineCommercialAgreementInfo(line) : { discount: 0, name: "" };

        const list = availableRewards.map((reward) => {
            const isApplied = line.promo_reward_id?.id === reward.id;
            const targetLabel = reward.reward_target_label || (
                reward.lines_product_id?.name ? `Línea: ${reward.lines_product_id.name}` :
                reward.scheme_id?.name ? `Esquema: ${reward.scheme_id.name}` : ""
            );
            const targetPart = targetLabel ? ` · ${targetLabel}` : "";
            const isOrderPromo = reward.discount_applicability === "order";
            const typeLabel = isOrderPromo ? ` [Orden]` : "";
            const label = `${reward.program_id?.name || "Promoción"}${typeLabel} - ${reward.discount}%${targetPart}${isApplied ? " ✓" : ""}`;
            return {
                id: reward.id,
                label: label,
                item: { reward, line },
            };
        });

        const isOrderPromo = line.promo_reward_id?.discount_applicability === "order" ||
            Boolean(order.order_promo_reward_id);

        if (line.promo_reward_id || order.order_promo_reward_id) {
            const fallbackText = partnerAgreement.discount > 0
                ? `${partnerAgreement.discount}% (${partnerAgreement.name})`
                : _t("precio de lista");
            const removeLabel = isOrderPromo
                ? _t("Sin promoción: quitar promoción de la orden (vuelve a %s)", fallbackText)
                : _t("Sin promoción: vuelve a %s", fallbackText);
            list.unshift({
                id: "remove_line_promo",
                label: removeLabel,
                item: { isRemoveAction: true, line, isOrderPromo },
                isDanger: true,
            });
        }

        const dialog = this.env?.services?.dialog || this.dialog;
        const notification = this.env?.services?.notification || this.notification;
        const pos = this.env?.services?.pos || this.pos;

        if (!list.length) {
            notification?.add(
                _t("No hay promociones disponibles para esta partida."),
                { type: "info" }
            );
            return;
        }

        const title = _t("Promociones para %s", line.product_id?.display_name || line.product_id?.name || "");

        dialog?.add(SelectionPopup, {
            title: title,
            list: list,
            getPayload: (selected) => {
                if (!selected) return;
                if (selected.isRemoveAction) {
                    if (selected.isOrderPromo || order.order_promo_reward_id) {
                        order._dropAllLinePromotions();
                    } else {
                        order._dropLinePromotion(selected.line);
                    }
                } else if (selected.reward) {
                    delete order.order_promo_opt_out;
                    if (selected.reward.discount_applicability === "order") {
                        order._applyLinePromotionDirect(selected.reward);
                    } else {
                        if (order.order_promo_reward_id) {
                            notification?.add(
                                _t("El pedido tiene una promoción global activa en todas sus líneas. Quítala primero para asignar promociones individuales."),
                                { type: "warning" }
                            );
                            return;
                        }
                        order._applySingleLinePromotion(selected.line, selected.reward);
                    }
                }
                if (typeof order._entintadosReconcileDiscounts === "function") {
                    order._entintadosReconcileDiscounts();
                }
                pos?.updateRewards?.();
            },
        });
    },

    onRemovePromotion(line) {
        const order = line?.order || line?.order_id || this.env?.services?.pos?.getOrder?.();
        if (order && typeof order._dropLinePromotion === "function") {
            order._dropLinePromotion(line);
            if (typeof order._entintadosReconcileDiscounts === "function") {
                order._entintadosReconcileDiscounts();
            }
        }
    },
});
