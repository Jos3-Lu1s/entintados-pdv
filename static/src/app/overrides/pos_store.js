/** @odoo-module */
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { getPartnerPricingRule, getPricelistItemRule, formatPricelistOriginLabel } from "@entintados_pdv/app/utils/pricing_rules";

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

    get canEditUnitPrice() {
        return Boolean(this.config?.user_can_edit_price ?? true);
    },

    get canEditDiscount() {
        return Boolean(this.config?.user_can_edit_discount ?? true);
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

    setPricelist(pricelist) {
        const res = super.setPricelist ? super.setPricelist(pricelist) : null;
        this._entintadosReconcileDiscounts();
        return res;
    },

    _entintadosHandlePartnerAssignment(partner) {
        this._entintadosReconcileDiscounts();
    },

    _entintadosReconcileDiscounts() {
        const partner = this.getPartner?.() || this.get_partner?.() || this.partner_id;
        const models = this.models || posStoreInstance?.models;
        const nativeAdvanceProduct = (this.config || this.config_id || posStoreInstance?.config)?.down_payment_product_id;

        // Determinar si existe una promoción global o de orden activa o aplicable
        let activeOrderPromo = this.order_promo_reward_id || null;
        if (activeOrderPromo) {
            const isValid = typeof this._isRewardValidForOrder === "function"
                ? this._isRewardValidForOrder(activeOrderPromo)
                : true;
            if (!isValid) {
                this.order_promo_reward_id = false;
                activeOrderPromo = null;
            }
        }

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
                    line.promo_reward_id = false;
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
                line.price_type = "original";
                line.manual_price = false;
            }

            // Si el cajero colocó un precio manual en un producto común (y no es base entintada), no pisar
            if (line.price_type === "manual" && !line.is_tinted_base) {
                line.price_origin = "manual";
                line.price_origin_label = "Modificado de forma manual";
            } else if (!line.is_tinted_base) {
                // Actualizar el precio unitario base según la tarifa activa de la orden
                const pricelist = this.pricelist_id || this.pricelist || this.config?.pricelist_id || posStoreInstance?.pricelist;
                let restoredPrice = null;
                if (product.product_tmpl_id?.getPrice) {
                    restoredPrice = product.product_tmpl_id.getPrice(
                        pricelist,
                        line.qty || 1,
                        line.getPriceExtra?.() || 0,
                        false,
                        product
                    );
                } else if (pricelist && typeof pricelist.get_price === "function") {
                    restoredPrice = pricelist.get_price(product, line.qty || 1);
                } else if (pricelist && typeof pricelist.getPrice === "function") {
                    restoredPrice = pricelist.getPrice(product, line.qty || 1);
                }
                if (restoredPrice === null || restoredPrice === undefined) {
                    restoredPrice = product.lst_price ?? line.price_unit;
                }
                if (typeof restoredPrice === "number" && !isNaN(restoredPrice) && line.price_unit !== restoredPrice) {
                    line._entintadosSettingPrice = true;
                    try {
                        if (typeof line.setUnitPrice === "function") {
                            line.setUnitPrice(restoredPrice);
                        } else if (typeof line.set_unit_price === "function") {
                            line.set_unit_price(restoredPrice);
                        } else {
                            line.price_unit = restoredPrice;
                        }
                    } finally {
                        delete line._entintadosSettingPrice;
                    }
                }

                // Evaluar si aplica una regla específica de la tarifa o catálogo base
                const itemRule = getPricelistItemRule(pricelist, product, line.qty || 1, models);
                if (itemRule) {
                    line.price_origin = "pricelist";
                    line.price_origin_label = formatPricelistOriginLabel(pricelist, itemRule, product, line.price_unit);
                } else {
                    line.price_origin = "list";
                    line.price_origin_label = "Precio de venta del producto";
                }
            }

            const agreementDiscount = rule.type === "discount" ? Number(rule.discount || 0) : 0;

            // 1. Si hay una promoción global activa en la orden (todo o nada):
            if (activeOrderPromo) {
                const isExcluded = typeof this._isLineExcludedFromPromo === "function"
                    ? this._isLineExcludedFromPromo(line, activeOrderPromo)
                    : Boolean(line.fixed_price_locked || line.pricing_rule_type === "fixed_price" || line.is_tint_colorant || line.combo_parent_id);

                if (!isExcluded) {
                    const promoDiscount = Number(activeOrderPromo.discount || 0);
                    const targetLabel = activeOrderPromo.reward_target_label || (
                        activeOrderPromo.lines_product_id?.name ? `Línea: ${activeOrderPromo.lines_product_id.name}` :
                        activeOrderPromo.scheme_id?.name ? `Esquema: ${activeOrderPromo.scheme_id.name}` : ""
                    );
                    const targetPart = targetLabel ? ` · ${targetLabel}` : "";
                    const promoOrigin = `${activeOrderPromo.program_id?.name || "Promoción"}${targetPart} - ${promoDiscount}%`;

                    line._entintadosSettingDiscount = true;
                    try {
                        if (typeof line.setDiscount === "function") {
                            line.setDiscount(promoDiscount);
                        } else if (typeof line.set_discount === "function") {
                            line.set_discount(promoDiscount);
                        } else {
                            line.discount = promoDiscount;
                        }
                        line.pricing_rule_type = "promo";
                        line.pricing_rule_origin = promoOrigin;
                        line.promo_reward_id = activeOrderPromo;
                        delete line.promo_opt_out;
                    } finally {
                        delete line._entintadosSettingDiscount;
                    }
                    continue;
                }
            }

            // 2. Si NO hay promoción global activa, evaluar si la partida porta una promoción específica válida
            if (line.promo_reward_id) {
                const reward = line.promo_reward_id;
                let isPromoStillValid = Boolean(reward.discount_applicability === "specific");
                if (isPromoStillValid && typeof this._isRewardValidForOrder === "function") {
                    isPromoStillValid = this._isRewardValidForOrder(reward);
                }
                if (isPromoStillValid && typeof this._isLineExcludedFromPromo === "function") {
                    if (this._isLineExcludedFromPromo(line, reward)) {
                        isPromoStillValid = false;
                    }
                }

                if (isPromoStillValid) {
                    const promoDiscount = Number(reward.discount || 0);
                    const targetLabel = reward.reward_target_label || (
                        reward.lines_product_id?.name ? `Línea: ${reward.lines_product_id.name}` :
                        reward.scheme_id?.name ? `Esquema: ${reward.scheme_id.name}` : ""
                    );
                    const targetPart = targetLabel ? ` · ${targetLabel}` : "";
                    const promoOrigin = `${reward.program_id?.name || "Promoción"}${targetPart} - ${promoDiscount}%`;

                    line._entintadosSettingDiscount = true;
                    try {
                        if (typeof line.setDiscount === "function") {
                            line.setDiscount(promoDiscount);
                        } else if (typeof line.set_discount === "function") {
                            line.set_discount(promoDiscount);
                        } else {
                            line.discount = promoDiscount;
                        }
                        line.pricing_rule_type = "promo";
                        line.pricing_rule_origin = promoOrigin;
                        delete line.promo_opt_out;
                    } finally {
                        delete line._entintadosSettingDiscount;
                    }
                    continue;
                }

                // Si ya no es válida o era una promoción de orden huérfana, limpiar referencia
                line.promo_reward_id = false;
            }

            // Aplicar descuento según acuerdo comercial pactado (excluyente frente a promoción)
            const targetDiscount = agreementDiscount;
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

            if (rule.type === "discount" && targetDiscount > 0) {
                line.pricing_rule_type = rule.originType || "product";
                line.pricing_rule_origin = rule.originLabel || "";
                line.pricing_rule_id = rule.rule?.id || false;
                line.promo_reward_id = false;
            } else {
                if (line.manual_discount && Number(line.discount) > 0) {
                    line.pricing_rule_type = "manual";
                    line.pricing_rule_origin = `Desc. Manual (${Number(line.discount).toFixed(1)}%)`;
                } else {
                    line.pricing_rule_type = "none";
                    line.pricing_rule_origin = "";
                    line.pricing_rule_id = false;
                }
                line.promo_reward_id = false;
            }
        }
    },
});
