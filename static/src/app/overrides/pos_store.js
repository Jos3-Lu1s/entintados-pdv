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
        if (order && typeof order._entintadosReconcileDiscounts === "function") {
            order._entintadosReconcileDiscounts();
        }
        return line;
    },

    async addLineToOrder(...args) {
        const line = await super.addLineToOrder(...args);
        const order = this.getOrder?.() || this.currentOrder;
        if (order && typeof order._entintadosReconcileDiscounts === "function") {
            order._entintadosReconcileDiscounts();
        }
        return line;
    },
});

patch(PosOrder.prototype, {
    set_partner(partner) {
        const res = super.set_partner ? super.set_partner(partner) : null;
        this._entintadosReconcileDiscounts();
        return res;
    },

    setPartner(partner) {
        const res = super.setPartner ? super.setPartner(partner) : null;
        this._entintadosReconcileDiscounts();
        return res;
    },

    _updateRewardLines(...args) {
        const beforeKeys = new Set(
            (this.lines || []).filter((l) => l.is_reward_line).map((l) => l.reward_identifier_code)
        );

        const result = super._updateRewardLines(...args);

        const afterLines = (this.lines || []).filter((l) => l.is_reward_line);

        for (const line of afterLines) {
            if (beforeKeys.has(line.reward_identifier_code)) {
                continue;
            }
            this._entintadosHandleNewReward(line);
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

        this._entintadosReconcileDiscounts();

        return result;
    },

    async _entintadosHandleNewReward(line) {
        const program = line.reward_id?.program_id;
        const isAutomaticPromotion =
            program?.program_type === "promotion" && program?.trigger === "auto";

        if (!isAutomaticPromotion || !posStoreInstance) {
            return;
        }

        const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
        const models = this.models || posStoreInstance?.models;
        const hasDiscountToLose = (this.lines || []).some((l) => {
            if (l.is_reward_line || l.fixed_price_locked) return false;
            const r = getPartnerPricingRule(models, partner, l.product_id);
            return r.type === "discount" && r.discount > 0;
        });

        if (!hasDiscountToLose) {
            // Sin descuentos porcentuales que colisionen; el precio fijo es intocable
            return;
        }

        if (!this.uiState) this.uiState = {};
        if (!this.uiState.promoDecisions) this.uiState.promoDecisions = {};

        const decisionKey = line.reward_identifier_code;
        if (this.uiState.promoDecisions[decisionKey]) {
            if (this.uiState.promoDecisions[decisionKey] === "declined" && this.lines.includes(line)) {
                this._entintadosInternalRemoval = true;
                this.removeOrderline(line);
                this._entintadosInternalRemoval = false;
            }
            return;
        }

        const choice = await new Promise((resolve) => {
            posStoreInstance.dialog.add(ConfirmationDialog, {
                title: "Promoción disponible",
                body: `Este pedido califica para la promoción "${program?.name}". El cliente cuenta con acuerdos comerciales de descuento. Aplicar la promoción anulará los porcentajes de descuento de la orden. Los precios fijos acordados se respetan siempre. ¿Qué deseas aplicar?`,
                confirmLabel: "Aplicar promoción",
                cancelLabel: "Mantener acuerdos de descuento",
                confirm: () => resolve("promo"),
                cancel: () => resolve("discount"),
            });
        });

        this.uiState.promoDecisions[decisionKey] = choice === "promo" ? "accepted" : "declined";

        if (choice === "discount" && this.lines.includes(line)) {
            this._entintadosInternalRemoval = true;
            this.removeOrderline(line);
            this._entintadosInternalRemoval = false;
        }

        this._entintadosReconcileDiscounts();
    },

    _entintadosReconcileDiscounts() {
        const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
        const models = this.models || posStoreInstance?.models;
        const hasAcceptedPromo = Object.values(this.uiState?.promoDecisions || {}).includes("accepted");

        for (const line of this.lines || []) {
            if (line.is_reward_line) {
                line.pricing_rule_type = "promo";
                line.pricing_rule_origin = "Promoción";
                line.pricing_rule_id = false;
                continue;
            }
            if (line.is_tint_colorant) {
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

            // Aplicar descuento según jerarquía (Niveles 2, 3, 4 y 5)
            const targetDiscount = (!hasAcceptedPromo && rule.type === "discount") ? rule.discount : 0;
            if (typeof line.setDiscount === "function") {
                line.setDiscount(targetDiscount);
            } else if (typeof line.set_discount === "function") {
                line.set_discount(targetDiscount);
            } else {
                line.discount = targetDiscount;
            }

            if (hasAcceptedPromo) {
                line.pricing_rule_type = "promo";
                line.pricing_rule_origin = "Promoción";
                line.pricing_rule_id = false;
            } else if (rule.type === "discount") {
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