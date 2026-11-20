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
        const nativeAdvanceProduct = (this.config || this.config_id || posStoreInstance?.config)?.down_payment_product_id;

        for (const line of this.lines || []) {
            // A refund keeps the price and discount actually paid, even if
            // the customer's commercial agreement has changed since the sale.
            if (line.refunded_orderline_id) {
                continue;
            }
            if (line.product_id?.product_tmpl_id?.is_accounting_advance || line.advance_source_ref ||
                (line.sale_order_origin_id && line.product_id?.id === nativeAdvanceProduct?.id)) {
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
                line.price_origin = "manual";
                line.price_origin_label = "Anticipo contable";
                continue;
            }
            if (line.is_reward_line) {
                line.price_origin = "promo";
                continue;
            }
            if (line.is_tint_colorant || line.product_id?.tint_role === "colorant") {
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
                line.price_origin = "tint";
                line.price_origin_label = "Colorante entintado";
                continue;
            }

            const product = line.product_id;
            const options = {
                uom: line.uom_id || line.product_uom_id || product?.uom_id,
                currency: line.currency || this.currency,
            };
            const rule = getPartnerPricingRule(models, partner, product, options);

            if (rule.type === "fixed_price") {
                // Prioridad 1: Precio Fijo en Producto (inviolable frente a promociones y descuentos)
                line._entintadosSettingPrice = true;
                line._entintadosSettingDiscount = true;
                try {
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
                    line.price_origin = line.is_tinted_base ? "tint" : (rule.priceOrigin || "agreement");
                    line.price_origin_label = line.is_tinted_base
                        ? "Precio del entintado: base más colorantes"
                        : (rule.originLabel || "Precio Fijo");
                } finally {
                    delete line._entintadosSettingPrice;
                    delete line._entintadosSettingDiscount;
                }
                continue;
            }

            // Si la línea tenía precio fijo bloqueado y ya no aplica regla de precio fijo:
            if (line.fixed_price_locked && rule.type !== "fixed_price") {
                line.fixed_price_locked = false;
                if (!line.is_tinted_base) {
                    line.price_type = "original";
                    line.manual_price = false;
                    const pricelist = this.pricelist || this.config?.pricelist_id || posStoreInstance?.pricelist;
                    let restoredPrice = null;
                    if (pricelist && typeof pricelist.get_price === "function") {
                        restoredPrice = pricelist.get_price(product, line.qty || 1);
                    } else if (pricelist && typeof pricelist.getPrice === "function") {
                        restoredPrice = pricelist.getPrice(product, line.qty || 1);
                    }
                    if (restoredPrice === null || restoredPrice === undefined) {
                        restoredPrice = product.lst_price ?? line.price_unit;
                    }
                    line._entintadosSettingPrice = true;
                    try {
                        if (typeof line.setUnitPrice === "function") {
                            line.setUnitPrice(restoredPrice);
                        } else {
                            line.price_unit = restoredPrice;
                        }
                    } finally {
                        delete line._entintadosSettingPrice;
                    }
                }
            }

            // Si el cajero colocó un precio manual en un producto común (y no es base entintada), no pisar
            if (line.price_type === "manual" && !line.is_tinted_base) {
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
                line.price_origin = "manual";
                line.price_origin_label = "Precio manual";
                continue;
            }

            // Aplicar descuento según jerarquía comercial (Niveles 2, 3, 4 y 5)
            const targetDiscount = rule.type === "discount" ? rule.discount : 0;
            line._entintadosSettingDiscount = true;
            try {
                if (typeof line.setDiscount === "function") {
                    line.setDiscount(targetDiscount);
                } else if (typeof line.set_discount === "function") {
                    line.set_discount(targetDiscount);
                } else {
                    line.discount = targetDiscount;
                }
            } finally {
                delete line._entintadosSettingDiscount;
            }

            if (rule.type === "discount") {
                line.pricing_rule_type = rule.originType || "product";
                line.pricing_rule_origin = rule.originLabel || "";
                line.pricing_rule_id = rule.rule?.id || false;
                line.price_origin = rule.priceOrigin || "agreement";
                line.price_origin_label = rule.originLabel || "";
            } else {
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
                if (!line.is_tinted_base && line.price_type !== "manual") {
                    line.price_origin = this.pricelist ? "pricelist" : "list";
                    line.price_origin_label = "";
                }
            }
        }
    },
});
