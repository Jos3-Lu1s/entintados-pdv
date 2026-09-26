/** @odoo-module */
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
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
        if (this._entintadosSettingPartner) {
            return super.set_partner ? super.set_partner(partner) : null;
        }
        this._entintadosSettingPartner = true;
        try {
            const res = super.set_partner ? super.set_partner(partner) : null;
            this._entintadosHandlePartnerAssignment(partner);
            return res;
        } finally {
            this._entintadosSettingPartner = false;
        }
    },

    setPartner(partner) {
        if (this._entintadosSettingPartner) {
            return super.setPartner ? super.setPartner(partner) : null;
        }
        this._entintadosSettingPartner = true;
        try {
            const res = super.setPartner ? super.setPartner(partner) : null;
            this._entintadosHandlePartnerAssignment(partner);
            return res;
        } finally {
            this._entintadosSettingPartner = false;
        }
    },

    _entintadosHandlePartnerAssignment(partner) {
        this._entintadosReconcileDiscounts();
    },

    _entintadosReconcileDiscounts() {
        const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
        const models = this.models || posStoreInstance?.models;

        for (const line of this.lines || []) {
            if (line.is_reward_line) {
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