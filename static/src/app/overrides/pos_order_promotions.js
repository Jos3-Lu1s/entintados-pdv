import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { formatOriginBadge } from "@entintados_pdv/app/utils/origin_badge";
import { agreementForLine } from "@entintados_pdv/app/utils/pricing_rules";
import {
    availablePromotionRewards,
    bestPromotionPlan,
    isLinePromotionReward,
    lineRewards,
    lossReason,
    orderLossReason,
} from "@entintados_pdv/app/utils/promotion_rules";

const sameRecord = (a, b) => Boolean(a && b && (a.id ?? a) === (b.id ?? b));

/**
 * Promociones por línea y de pedido en la caja, con las reglas de Ventas
 * (`sale.order._reevaluate_line_promotions` y compañía). Las reglas puras viven en
 * `utils/promotion_rules.js`; aquí solo se escriben los valores en las líneas.
 */
patch(PosOrder.prototype, {
    /** Las promociones por línea se eligen en la línea, no son recompensas reclamables. */
    getClaimableRewards() {
        return super
            .getClaimableRewards(...arguments)
            .filter(({ reward }) => !isLinePromotionReward(reward));
    },

    _writeLinePromotion(line, reward) {
        line.discount = reward.discount;
        line.pricing_rule_type = "promo";
        line.pricing_rule_origin = formatOriginBadge(reward.program_id?.name, reward.discount);
        line.pricing_rule_id = false;
        line.promo_reward_id = reward;
    },

    /** Vuelve la línea a su acuerdo vigente, como `_promotion_fallback_values` de Ventas. */
    _writePromotionFallback(line) {
        const rule = agreementForLine(this, line);
        line.promo_reward_id = false;
        line.pricing_rule_id = rule.rule || false;
        if (rule.type === "fixed_price") {
            line.discount = 0;
            line.pricing_rule_type = "fixed_price";
            line.pricing_rule_origin = rule.originLabel || "";
            line._entintadosSettingPrice = true;
            try {
                line.setUnitPrice(rule.price);
            } finally {
                delete line._entintadosSettingPrice;
            }
            line.price_type = "manual";
            line.fixed_price_locked = true;
            line.price_origin = "fixed_price";
            line.price_origin_label = rule.originLabel || "";
        } else if (rule.type === "discount") {
            line.discount = Number(rule.discount || 0);
            line.pricing_rule_type = rule.originType;
            line.pricing_rule_origin = rule.originLabel || "";
        } else {
            line.discount = 0;
            line.pricing_rule_type = "none";
            line.pricing_rule_origin = "";
        }
    },

    /**
     * Aplica `reward` a la línea, como `action_set_line_promotion`. Una promoción de pedido
     * se activa en todo el pedido. Devuelve `true` o el mensaje de por qué no se aplicó.
     */
    applyLinePromotion(line, reward) {
        if (reward.discount_applicability === "order") {
            return this.setOrderPromotion(reward);
        }
        if (this.order_promo_reward_id) {
            return _t(
                "El pedido tiene una promoción de pedido activa en todas sus líneas. Quítala primero para asignar promociones individuales."
            );
        }
        const available = lineRewards(this).get(line) || [];
        if (!available.some((item) => sameRecord(item, reward))) {
            return _t("La promoción «%(reward)s» no está disponible para la línea «%(line)s».", {
                reward: reward.program_id?.name || "",
                line: line.getFullProductName?.() || line.product_id?.display_name || "",
            });
        }
        this._writeLinePromotion(line, reward);
        return true;
    },

    /** Quita la promoción de las líneas y las devuelve a su acuerdo vigente. */
    dropLinePromotion(lines) {
        for (const line of lines.filter((item) => item.promo_reward_id)) {
            this._writePromotionFallback(line);
        }
    },

    /** Activa `reward` como promoción de pedido en todas las líneas elegibles. */
    setOrderPromotion(reward) {
        this.order_promo_reward_id = reward;
        this.applyOrderPromotionToLines();
        return true;
    },

    /** Quita la promoción de pedido de todas sus líneas. */
    removeOrderPromotion() {
        const reward = this.order_promo_reward_id;
        if (reward) {
            this.dropLinePromotion(
                this.getOrderlines().filter((line) => sameRecord(line.promo_reward_id, reward))
            );
        }
        this.order_promo_reward_id = false;
    },

    /** Aplica la promoción de pedido a las líneas elegibles que aún no la tienen. */
    applyOrderPromotionToLines() {
        const reward = this.order_promo_reward_id;
        if (!reward) {
            return [];
        }
        const available = lineRewards(this);
        const changed = [];
        for (const [line, rewards] of available) {
            if (!sameRecord(line.promo_reward_id, reward) && rewards.some((item) => sameRecord(item, reward))) {
                this._writeLinePromotion(line, reward);
                changed.push(line);
            }
        }
        return changed;
    },

    /**
     * «Mejores promociones», como `_apply_best_line_promotions`. Devuelve las líneas que
     * cambiaron.
     */
    applyBestLinePromotions() {
        const plan = bestPromotionPlan(this);
        if (plan.orderReward) {
            this.setOrderPromotion(plan.orderReward);
            return this.getOrderlines().filter((line) => sameRecord(line.promo_reward_id, plan.orderReward));
        }
        for (const [line, reward] of plan.lines) {
            this._writeLinePromotion(line, reward);
        }
        return [...plan.lines.keys()];
    },

    /**
     * Retira las promociones que ya no proceden, actualiza el % de las vigentes y extiende la
     * promoción de pedido, como `_reevaluate_line_promotions`. Devuelve los avisos.
     */
    reevaluateLinePromotions() {
        const notices = [];
        const lines = this.getOrderlines();
        for (const line of lines) {
            const orphaned =
                line.pricing_rule_type === "promo" &&
                !line.promo_reward_id &&
                !line.reward_id &&
                !line.is_reward_line &&
                !line.combo_parent_id;
            if (orphaned) {
                this._writePromotionFallback(line);
            }
        }

        const orderReward = this.order_promo_reward_id;
        if (orderReward && !availablePromotionRewards(this).some((item) => sameRecord(item, orderReward))) {
            const reason = orderLossReason(this, orderReward);
            this.removeOrderPromotion();
            notices.push({
                type: "warning",
                title: _t("Promoción de pedido retirada"),
                message: _t(
                    "El pedido dejó la promoción %(program)s: %(reason)s. Sus líneas volvieron a su acuerdo.",
                    { program: orderReward.program_id?.name || "", reason }
                ),
            });
        }

        // Al volver al acuerdo, una línea puede dejar a otro programa debajo de su mínimo.
        let promoLines = lines.filter((line) => line.promo_reward_id);
        while (promoLines.length) {
            const available = lineRewards(this);
            const lost = promoLines.filter(
                (line) => !(available.get(line) || []).some((item) => sameRecord(item, line.promo_reward_id))
            );
            if (!lost.length) {
                break;
            }
            for (const line of lost) {
                const reward = line.promo_reward_id;
                const reason = lossReason(this, line, reward);
                this._writePromotionFallback(line);
                const product = line.getFullProductName?.() || line.product_id?.display_name || "";
                notices.push({
                    type: "warning",
                    title: _t("Promoción retirada: %s", product),
                    message: _t(
                        "La línea %(product)s dejó la promoción %(program)s: %(reason)s. Volvió a %(origin)s.",
                        {
                            product,
                            program: reward.program_id?.name || "",
                            reason,
                            origin: line.pricing_rule_origin || _t("sin descuento"),
                        }
                    ),
                });
            }
            promoLines = promoLines.filter((line) => !lost.includes(line));
        }

        for (const line of promoLines) {
            const reward = line.promo_reward_id;
            const changed = Math.abs(Number(line.discount || 0) - Number(reward.discount || 0)) > 1e-9;
            if (changed) {
                const product = line.getFullProductName?.() || line.product_id?.display_name || "";
                notices.push({
                    type: "warning",
                    title: _t("Promoción actualizada: %s", product),
                    message: _t(
                        "La línea %(product)s cambió su promoción %(program)s a %(discount)s %.",
                        { product, program: reward.program_id?.name || "", discount: reward.discount }
                    ),
                });
            }
            if (changed || line.pricing_rule_origin !== formatOriginBadge(reward.program_id?.name, reward.discount)) {
                this._writeLinePromotion(line, reward);
            }
        }

        this.applyOrderPromotionToLines();
        return notices;
    },
});
