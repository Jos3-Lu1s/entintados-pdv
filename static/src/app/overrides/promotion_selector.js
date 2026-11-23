import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { agreementForLine } from "@entintados_pdv/app/utils/pricing_rules";
import {
    admitsLinePromotion,
    availablePromotionRewards,
    lineRewards,
} from "@entintados_pdv/app/utils/promotion_rules";

const sameRecord = (a, b) => Boolean(a && b && (a.id ?? a) === (b.id ?? b));

function rewardLabel(reward, applied) {
    const target = reward.reward_target_label ? ` · ${reward.reward_target_label}` : "";
    const scope = reward.discount_applicability === "order" ? " [Orden]" : "";
    return `${reward.program_id?.name || ""}${scope} - ${reward.discount}%${target}${applied ? " ✓" : ""}`;
}

/** Destino de «Sin promoción»: el acuerdo vigente de la línea, como el selector de Ventas. */
function fallbackLabel(order, line) {
    return agreementForLine(order, line).originLabel || _t("sin acuerdo");
}

/** Quita la promoción de la línea; la de pedido es de todo o nada y se quita del pedido. */
function removeLinePromotion(order, line) {
    if (order.order_promo_reward_id && sameRecord(line.promo_reward_id, order.order_promo_reward_id)) {
        order.removeOrderPromotion();
    } else {
        order.dropLinePromotion([line]);
    }
}

patch(Orderline.prototype, {
    canHavePromotion(line) {
        return admitsLinePromotion(line);
    },

    getPromotionButtonTitle(line) {
        if (line.promo_reward_id) {
            return _t("Promoción aplicada: %s. Clic para cambiar o quitar.", line.pricing_rule_origin || "");
        }
        return _t("Ver y asignar promociones disponibles para esta partida.");
    },

    async onSelectLinePromotion(line) {
        const order = line.order_id;
        const { dialog, notification, pos } = this.env.services;
        const rewards = [...(lineRewards(order).get(line) || [])];
        if (line.promo_reward_id && !rewards.some((reward) => sameRecord(reward, line.promo_reward_id))) {
            rewards.unshift(line.promo_reward_id);
        }
        const list = rewards.map((reward) => ({
            id: reward.id,
            label: rewardLabel(reward, sameRecord(reward, line.promo_reward_id)),
            item: { reward },
        }));
        if (line.promo_reward_id) {
            const isOrderPromo = sameRecord(line.promo_reward_id, order.order_promo_reward_id);
            list.unshift({
                id: "remove_line_promo",
                label: isOrderPromo
                    ? _t("Sin promoción: quitar promoción de la orden (vuelve a %s)", fallbackLabel(order, line))
                    : _t("Sin promoción: vuelve a %s", fallbackLabel(order, line)),
                item: { remove: true },
            });
        }
        if (!list.length) {
            notification.add(_t("No hay promociones disponibles para esta partida."), { type: "info" });
            return;
        }
        dialog.add(SelectionPopup, {
            title: _t("Promociones para %s", line.getFullProductName?.() || line.product_id?.display_name || ""),
            list,
            getPayload: (selected) => {
                if (selected?.remove) {
                    removeLinePromotion(order, line);
                } else if (selected?.reward && !sameRecord(selected.reward, line.promo_reward_id)) {
                    const result = order.applyLinePromotion(line, selected.reward);
                    if (result !== true) {
                        notification.add(result, { type: "warning" });
                    }
                }
                pos.updateRewards();
            },
        });
    },

    onRemovePromotion(line) {
        removeLinePromotion(line.order_id, line);
        this.env.services.pos.updateRewards();
    },
});

patch(ControlButtons.prototype, {
    /** Recompensas nativas del core más las promociones por línea y de pedido de la caja. */
    async clickRewards() {
        const order = this.pos.getOrder();
        const selectedLine = order?.getSelectedOrderline();
        const promotions = order ? availablePromotionRewards(order) : [];
        const list = this.getPotentialRewards().map((item) => ({
            id: item.reward.id,
            label: item.reward.program_id.name,
            description: `Add "${item.reward.description}"`,
            item: { native: item },
        }));

        for (const reward of promotions) {
            const applied =
                sameRecord(selectedLine?.promo_reward_id, reward) ||
                sameRecord(order.order_promo_reward_id, reward);
            list.push({ id: `promo_${reward.id}`, label: rewardLabel(reward, applied), item: { reward } });
        }
        if (promotions.length) {
            list.unshift({
                id: "apply_best_promos",
                label: _t("⚡ Aplicar mejores promociones"),
                description: _t("Aplica la mejor promoción global o por partida si mejora el descuento actual"),
                item: { best: true },
            });
        }
        if (order?.order_promo_reward_id) {
            list.unshift({
                id: "remove_order_promo",
                label: _t("Sin promoción: quitar promoción de la orden"),
                description: _t("Vuelve todas las líneas a sus acuerdos vigentes"),
                item: { removeOrder: true },
            });
        } else if (selectedLine?.promo_reward_id) {
            list.unshift({
                id: "remove_selected_line_promo",
                label: _t("Sin promoción: vuelve a %s", fallbackLabel(order, selectedLine)),
                description: _t("Quitar promoción de la línea seleccionada"),
                item: { removeLine: selectedLine },
            });
        } else if (order?.getOrderlines().some((line) => line.promo_reward_id)) {
            list.push({
                id: "remove_all_line_promos",
                label: _t("Sin promoción: quitar promociones del pedido"),
                description: _t("Vuelve todas las líneas a sus acuerdos vigentes"),
                item: { removeAll: true },
            });
        }
        if (!list.length) {
            return;
        }

        this.dialog.add(SelectionPopup, {
            title: _t("Available rewards"),
            list,
            getPayload: (selected) => {
                if (!selected) {
                    return;
                }
                if (selected.native) {
                    const { reward, coupon_id, potentialQty } = selected.native;
                    this._applyReward(reward, coupon_id, potentialQty);
                    return;
                }
                if (selected.best) {
                    const changed = order.applyBestLinePromotions();
                    this.notification.add(
                        changed.length
                            ? _t("Se aplicó la mejor promoción en %s partida(s).", changed.length)
                            : _t("Ninguna promoción disponible mejora el descuento actual de las líneas."),
                        { type: changed.length ? "success" : "info" }
                    );
                } else if (selected.removeOrder) {
                    order.removeOrderPromotion();
                } else if (selected.removeLine) {
                    removeLinePromotion(order, selected.removeLine);
                } else if (selected.removeAll) {
                    order.dropLinePromotion(order.getOrderlines());
                } else if (selected.reward) {
                    this._applyPromotion(order, selectedLine, selected.reward);
                }
                this.pos.updateRewards();
            },
        });
    },

    /**
     * Una promoción de pedido se activa en todo el pedido. Una de producto va a la línea
     * seleccionada si la admite; si no, a todas las líneas a las que se ofrece.
     */
    _applyPromotion(order, selectedLine, reward) {
        if (reward.discount_applicability === "order") {
            order.setOrderPromotion(reward);
            return;
        }
        const offered = lineRewards(order);
        const isOffered = (rewards) => (rewards || []).some((item) => sameRecord(item, reward));
        const lines = isOffered(offered.get(selectedLine))
            ? [selectedLine]
            : [...offered].filter(([, rewards]) => isOffered(rewards)).map(([line]) => line);
        for (const line of lines) {
            const result = order.applyLinePromotion(line, reward);
            if (result !== true) {
                this.notification.add(result, { type: "warning" });
                return;
            }
        }
    },
});
