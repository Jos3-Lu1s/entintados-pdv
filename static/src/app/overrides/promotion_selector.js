import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { formatCurrency } from "@web/core/currency";
import { promoHintLines } from "@entintados_pdv/common/promo_hint_rows";
import { agreementForLine } from "@entintados_pdv/app/utils/pricing_rules";
import {
    availablePromotionRewards,
    linePromotionInfo,
    lineRewards,
    promotionLineCount,
} from "@entintados_pdv/app/utils/promotion_rules";

const sameRecord = (a, b) => Boolean(a && b && (a.id ?? a) === (b.id ?? b));

/** Texto de una promoción, el mismo que la estrella: `Programa · Objetivo - 20 % (todo el pedido)`. */
function rewardText(reward) {
    const option = {
        reward_id: reward.id,
        name: reward.program_id?.name || "",
        target: reward.reward_target_label || "",
        discount: reward.discount,
        difference: 0,
        applied: false,
        scope: reward.discount_applicability,
    };
    return promoHintLines({ options: [option], fallback: false }, _t, String)[0].text;
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
    /** La estrella sale solo si la línea tiene algo que ofrecer o informar, como en Ventas. */
    canHavePromotion(line) {
        return Boolean(linePromotionInfo(line.order_id, line));
    },

    isPromotionStarFilled(line) {
        const info = linePromotionInfo(line.order_id, line);
        return Boolean(info?.options?.some((option) => option.applied));
    },

    getPromotionButtonTitle(line) {
        const info = linePromotionInfo(line.order_id, line);
        if (info?.from_package) {
            return _t("Promoción del paquete");
        }
        if (line.promo_reward_id) {
            return _t(
                "Promoción aplicada: %s. Toca para cambiarla o quitarla.",
                line.pricing_rule_origin || ""
            );
        }
        return _t("Promociones disponibles para esta línea.");
    },

    async onSelectLinePromotion(line) {
        const order = line.order_id;
        const { dialog, notification, pos } = this.env.services;
        const info = linePromotionInfo(order, line);
        if (info?.from_package) {
            const packageLine = line.combo_parent_id;
            notification.add(
                _t(
                    "Promoción del paquete «%(package)s»: %(badge)s. Se cambia en el paquete.",
                    {
                        package: packageLine.product_id?.name || packageLine.product_id?.display_name || "",
                        badge: packageLine.pricing_rule_origin || "",
                    }
                ),
                { type: "info" }
            );
            return;
        }
        const rows = promoHintLines(info, _t, (amount) => formatCurrency(amount, order.currency.id));
        if (!rows.length) {
            notification.add(_t("No hay promociones disponibles para esta línea."), { type: "info" });
            return;
        }
        const rewards = [...(lineRewards(order).get(line) || [])];
        if (line.promo_reward_id) {
            rewards.push(line.promo_reward_id);
        }
        dialog.add(SelectionPopup, {
            title: _t("Promociones para %s", line.getFullProductName?.() || line.product_id?.display_name || ""),
            list: rows.map((row) => ({
                id: row.key,
                label: row.text,
                description: row.detail,
                isSelected: row.applied,
                item: row,
            })),
            getPayload: (row) => {
                if (!row || row.applied) {
                    return;
                }
                if (!row.rewardId) {
                    removeLinePromotion(order, line);
                } else {
                    const reward = rewards.find((item) => sameRecord(item, row.rewardId));
                    const result = reward ? order.applyLinePromotion(line, reward) : true;
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
    /** Líneas con promociones disponibles sin aplicar: el `(N)` del botón de promociones. */
    get promotionLineCount() {
        const order = this.pos.getOrder();
        return order ? promotionLineCount(order) : 0;
    },

    /** Recompensas nativas del core más las promociones por línea y de pedido de la caja. */
    async clickRewards() {
        const order = this.pos.getOrder();
        const selectedLine = order?.getSelectedOrderline();
        const promotions = order ? availablePromotionRewards(order) : [];
        const list = this.getPotentialRewards().map((item) => ({
            id: item.reward.id,
            label: item.reward.program_id.name,
            description: _t("Agregar «%s»", item.reward.description),
            item: { native: item },
        }));

        for (const reward of promotions) {
            list.push({
                id: `promo_${reward.id}`,
                label: rewardText(reward),
                isSelected:
                    sameRecord(selectedLine?.promo_reward_id, reward) ||
                    sameRecord(order.order_promo_reward_id, reward),
                item: { reward },
            });
        }
        if (promotions.length) {
            list.unshift({
                id: "apply_best_promos",
                label: _t("Aplicar mejores promociones"),
                description: _t(
                    "Aplica la mejor promoción de pedido o, si no hay, la mejor de cada línea cuando mejora su descuento."
                ),
                item: { best: true },
            });
        }
        if (order?.order_promo_reward_id) {
            list.unshift({
                id: "remove_order_promo",
                label: _t("Quitar la promoción del pedido"),
                description: _t("Todas las líneas vuelven a su acuerdo."),
                item: { removeOrder: true },
            });
        } else if (selectedLine?.promo_reward_id) {
            list.unshift({
                id: "remove_selected_line_promo",
                label: _t("Sin promoción: vuelve a %s", fallbackLabel(order, selectedLine)),
                description: _t("Quita la promoción de la línea seleccionada."),
                item: { removeLine: selectedLine },
            });
        } else if (order?.getOrderlines().some((line) => line.promo_reward_id)) {
            list.push({
                id: "remove_all_line_promos",
                label: _t("Sin promoción en todo el pedido"),
                description: _t("Todas las líneas vuelven a su acuerdo."),
                item: { removeAll: true },
            });
        }
        if (!list.length) {
            return;
        }

        this.dialog.add(SelectionPopup, {
            title: _t("Promociones y recompensas"),
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
                            ? _t("Se aplicó la mejor promoción en %s línea(s).", changed.length)
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
