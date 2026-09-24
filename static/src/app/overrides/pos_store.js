/** @odoo-module */
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { getPartnerPricingRule } from "@entintados_pdv/app/utils/pricing_rules";

let posStoreInstance = null;

patch(PosStore.prototype, {
    editPartnerContext(partner) {
        return {
            ...super.editPartnerContext(partner),
            default_is_customer: true,
            entintados_partner_scope: "customer",
        };
    },

    setup(...args) {
        const result = super.setup(...args);
        posStoreInstance = this;
        return result;
    },

    async addLineToCurrentOrder(...args) {
        const line = await super.addLineToCurrentOrder(...args);
        const order = this.getOrder?.() || this.currentOrder;
        if (order) {
            if (typeof order._entintadosCheckActivePromoQualification === "function") {
                order._entintadosCheckActivePromoQualification();
            }
            if (typeof order._entintadosReconcileDiscounts === "function") {
                order._entintadosReconcileDiscounts();
            }
        }
        return line;
    },

    async addLineToOrder(...args) {
        const line = await super.addLineToOrder(...args);
        const order = this.getOrder?.() || this.currentOrder;
        if (order) {
            if (typeof order._entintadosCheckActivePromoQualification === "function") {
                order._entintadosCheckActivePromoQualification();
            }
            if (typeof order._entintadosReconcileDiscounts === "function") {
                order._entintadosReconcileDiscounts();
            }
        }
        return line;
    },
});

patch(PosOrder.prototype, {
    set_partner(partner) {
        const res = super.set_partner ? super.set_partner(partner) : null;
        this._entintadosCheckActivePromoQualification?.();
        this._entintadosReconcileDiscounts();
        return res;
    },

    setPartner(partner) {
        const res = super.setPartner ? super.setPartner(partner) : null;
        this._entintadosCheckActivePromoQualification?.();
        this._entintadosReconcileDiscounts();
        return res;
    },

    async _applyReward(reward, coupon_id, args) {
        if (!reward || reward.reward_type !== "discount" || reward.discount_mode !== "percent") {
            return super._applyReward ? super._applyReward(reward, coupon_id, args) : true;
        }

        const program = reward.program_id;
        const decisionKey = String(reward.id);
        if (!this.uiState) this.uiState = {};
        if (!this.uiState.promoDecisions) this.uiState.promoDecisions = {};
        if (!this.uiState.disabledRewards) this.uiState.disabledRewards = new Set();

        const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
        const models = this.models || posStoreInstance?.models;

        // 1. Si ya se declinó esta recompensa para esta orden:
        if (this.uiState.promoDecisions[decisionKey] === "declined") {
            this.uiState.disabledRewards.add(reward.id);
            this._entintadosReconcileDiscounts();
            return false;
        }

        // 2. Evaluar si el cliente tiene acuerdos de descuento activos colisionantes
        const hasCollidingDiscount = (this.lines || []).some((l) => {
            if (l.is_reward_line || l.is_tint_colorant || l.fixed_price_locked) return false;
            const r = getPartnerPricingRule(models, partner, l.product_id);
            return r.type === "discount" && r.discount > 0;
        });

        // 3. Si colisiona y no hay decisión previa, mostrar ConfirmationDialog
        if (!this.uiState.promoDecisions[decisionKey]) {
            if (hasCollidingDiscount && posStoreInstance?.dialog) {
                const choice = await new Promise((resolve) => {
                    posStoreInstance.dialog.add(ConfirmationDialog, {
                        title: "Promoción disponible",
                        body: `Este pedido califica para la promoción "${program?.name || reward.description}". El cliente cuenta con acuerdos comerciales de descuento. Aplicar la promoción anulará los porcentajes de descuento de la orden. Los precios fijos acordados se respetan siempre. ¿Qué deseas aplicar?`,
                        confirmLabel: "Aplicar promoción",
                        cancelLabel: "Mantener acuerdos de descuento",
                        confirm: () => resolve("promo"),
                        cancel: () => resolve("discount"),
                    });
                });

                if (choice === "discount") {
                    this.uiState.promoDecisions[decisionKey] = "declined";
                    this.uiState.disabledRewards.add(reward.id);
                    if (this.uiState.activePromoReward?.id === reward.id) {
                        this.uiState.activePromoReward = null;
                    }
                    this._entintadosReconcileDiscounts();
                    return false;
                }

                this.uiState.promoDecisions[decisionKey] = "accepted";
            } else {
                this.uiState.promoDecisions[decisionKey] = "accepted";
            }
        }

        // 4. Si la promoción es aceptada (o el cliente no tenía acuerdos de descuento):
        this.uiState.activePromoReward = reward;
        this._entintadosApplyInlinePromoDiscount(reward);

        // Eliminar o evitar cualquier línea independiente de recompensa (is_reward_line)
        const rewardLines = (this.lines || []).filter(
            (l) => l.is_reward_line && (l.reward_id?.id === reward.id || l.reward_identifier_code)
        );
        for (const rl of rewardLines) {
            this._entintadosInternalRemoval = true;
            if (typeof rl.delete === "function") {
                rl.delete();
            } else {
                this.removeOrderline(rl);
            }
            this._entintadosInternalRemoval = false;
        }

        return true;
    },

    _updateRewardLines(...args) {
        const result = super._updateRewardLines ? super._updateRewardLines(...args) : false;

        const promoRewardLines = (this.lines || []).filter(
            (l) => l.is_reward_line && l.reward_id?.reward_type === "discount" && l.reward_id?.discount_mode === "percent"
        );
        for (const rl of promoRewardLines) {
            this._entintadosInternalRemoval = true;
            if (typeof rl.delete === "function") {
                rl.delete();
            } else {
                this.removeOrderline(rl);
            }
            this._entintadosInternalRemoval = false;
        }

        this._entintadosReconcileDiscounts();
        return result;
    },

    // Interceptamos la eliminación de líneas para detectar cuando
    // el cajero borra manualmente una línea de recompensa.
    removeOrderline(line) {
        const wasReward = line?.is_reward_line;
        const decisionKey = line?.reward_identifier_code;
        const isInternalRemoval = this._entintadosInternalRemoval === true;

        const result = super.removeOrderline(line);

        if (wasReward && decisionKey && !isInternalRemoval) {
            if (this.uiState?.promoDecisions) {
                delete this.uiState.promoDecisions[decisionKey];
            }
        }

        this._entintadosCheckActivePromoQualification();
        this._entintadosReconcileDiscounts();

        return result;
    },

    _entintadosCheckActivePromoQualification() {
        if (!this.uiState?.activePromoReward) return;
        const reward = this.uiState.activePromoReward;
        const program = reward.program_id;
        if (!program) return;

        let qualifies = true;
        if (typeof this._programIsApplicable === "function" && !this._programIsApplicable(program)) {
            qualifies = false;
        }
        if (qualifies && typeof this._canGenerateRewards === "function") {
            if (!this._canGenerateRewards(program, this.priceIncl, this.priceExcl)) {
                qualifies = false;
            }
        }

        const nonRewardLines = (this.lines || []).filter((l) => !l.is_reward_line);
        if (!nonRewardLines.length) {
            qualifies = false;
        }

        if (!qualifies) {
            const decisionKey = String(reward.id);
            if (this.uiState.promoDecisions) {
                delete this.uiState.promoDecisions[decisionKey];
            }
            this.uiState.activePromoReward = null;
            if (this.uiState.disabledRewards) {
                this.uiState.disabledRewards.delete(reward.id);
            }
        }
    },

    async _entintadosHandleNewReward(line) {
        if (!line?.reward_id) return;
        return this._applyReward(line.reward_id, line.coupon_id?.id);
    },

    _entintadosApplyInlinePromoDiscount(reward) {
        if (!reward) return;
        const program = reward.program_id;
        const promoDiscount = Math.min(reward.discount, 100);
        const originLabel = `Promoción: ${program?.name || reward.description || ""}`;

        let eligibleLines = [];
        if (reward.discount_applicability === "order") {
            eligibleLines = (this.lines || []).filter(
                (l) => !l.is_reward_line && !l.is_tint_colorant && l.product_id?.tint_role !== "colorant"
            );
        } else if (reward.discount_applicability === "specific") {
            eligibleLines = (this._getSpecificDiscountableLines?.(reward) || []).filter(
                (l) => !l.is_reward_line && !l.is_tint_colorant && l.product_id?.tint_role !== "colorant"
            );
        } else if (reward.discount_applicability === "cheapest") {
            const cheapest = this._getCheapestLine?.(reward);
            if (cheapest && !cheapest.is_tint_colorant && cheapest.product_id?.tint_role !== "colorant") {
                eligibleLines = [cheapest];
            }
        }

        const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
        const models = this.models || posStoreInstance?.models;

        for (const line of this.lines || []) {
            if (line.is_reward_line) continue;
            if (line.is_tint_colorant || line.product_id?.tint_role === "colorant") {
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
                continue;
            }

            const rule = getPartnerPricingRule(models, partner, line.product_id);

            // 1. Precios fijos: inviolables frente a promociones y descuentos
            if (rule.type === "fixed_price" || line.fixed_price_locked) {
                if (!line.is_tinted_base) {
                    if (typeof line.setUnitPrice === "function") {
                        line.setUnitPrice(rule.price);
                    } else if (typeof line.set_unit_price === "function") {
                        line.set_unit_price(rule.price);
                    } else {
                        line.price_unit = rule.price;
                    }
                    line.price_type = "manual";
                    line.manual_price = true;
                }
                line.fixed_price_locked = true;

                if (typeof line.setDiscount === "function") {
                    line.setDiscount(0);
                } else if (typeof line.set_discount === "function") {
                    line.set_discount(0);
                } else {
                    line.discount = 0;
                }

                line.pricing_rule_type = rule.originType || "fixed_price";
                line.pricing_rule_origin = rule.originLabel || "Precio Fijo";
                line.pricing_rule_id = rule.rule?.id || false;
                continue;
            }

            // 2. Líneas elegibles de la promoción
            if (eligibleLines.includes(line)) {
                if (typeof line.setDiscount === "function") {
                    line.setDiscount(promoDiscount);
                } else if (typeof line.set_discount === "function") {
                    line.set_discount(promoDiscount);
                } else {
                    line.discount = promoDiscount;
                }
                line.pricing_rule_type = "promo";
                line.pricing_rule_origin = originLabel;
                line.pricing_rule_id = false;
            } else {
                // 3. Exclusividad: anular acuerdos comerciales de descuento en las demás líneas
                if (typeof line.setDiscount === "function") {
                    line.setDiscount(0);
                } else if (typeof line.set_discount === "function") {
                    line.set_discount(0);
                } else {
                    line.discount = 0;
                }
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
            }
        }
    },

    _entintadosReconcileDiscounts() {
        if (this.uiState?.activePromoReward) {
            this._entintadosApplyInlinePromoDiscount(this.uiState.activePromoReward);
            return;
        }

        const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
        const models = this.models || posStoreInstance?.models;

        for (const line of this.lines || []) {
            if (line.is_reward_line) {
                line.pricing_rule_type = "promo";
                line.pricing_rule_origin = "Promoción";
                line.pricing_rule_id = false;
                continue;
            }
            if (line.is_tint_colorant || line.product_id?.tint_role === "colorant") {
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
                continue;
            }

            const product = line.product_id;
            const rule = getPartnerPricingRule(models, partner, product);

            if (rule.type === "fixed_price") {
                // Prioridad 1: Precio Fijo en Producto (inviolable frente a promociones y descuentos)
                if (!line.is_tinted_base) {
                    if (typeof line.setUnitPrice === "function") {
                        line.setUnitPrice(rule.price);
                    } else if (typeof line.set_unit_price === "function") {
                        line.set_unit_price(rule.price);
                    } else {
                        line.price_unit = rule.price;
                    }
                    line.price_type = "manual";
                    line.manual_price = true;
                }
                line.fixed_price_locked = true;

                if (typeof line.setDiscount === "function") {
                    line.setDiscount(0);
                } else if (typeof line.set_discount === "function") {
                    line.set_discount(0);
                } else {
                    line.discount = 0;
                }

                line.pricing_rule_type = rule.originType || "fixed_price";
                line.pricing_rule_origin = rule.originLabel || "Precio Fijo";
                line.pricing_rule_id = rule.rule?.id || false;
                continue;
            }

            // Si la línea tenía precio fijo bloqueado y ya no aplica regla de precio fijo:
            if (line.fixed_price_locked && rule.type !== "fixed_price") {
                line.fixed_price_locked = false;
                if (!line.is_tinted_base) {
                    line.price_type = "original";
                    line.manual_price = false;
                    const originalPrice = line.product_id?.lst_price ?? line.price_unit;
                    if (typeof line.setUnitPrice === "function") {
                        line.setUnitPrice(originalPrice);
                    } else {
                        line.price_unit = originalPrice;
                    }
                }
            }

            // Si el cajero colocó un precio manual en un producto común (y no es base entintada), no pisar
            if (line.price_type === "manual" && !line.is_tinted_base) {
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
                continue;
            }

            // Aplicar descuento según jerarquía comercial (Niveles 2, 3, 4 y 5)
            const targetDiscount = rule.type === "discount" ? rule.discount : 0;
            if (typeof line.setDiscount === "function") {
                line.setDiscount(targetDiscount);
            } else if (typeof line.set_discount === "function") {
                line.set_discount(targetDiscount);
            } else {
                line.discount = targetDiscount;
            }

            if (rule.type === "discount") {
                line.pricing_rule_type = rule.originType || "product";
                line.pricing_rule_origin = rule.originLabel || "";
                line.pricing_rule_id = rule.rule?.id || false;
            } else {
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
            }
        }
    },
});