/** @odoo-module */
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { _t } from "@web/core/l10n/translation";
import { getPartnerPricingRule, getPricelistItemRule, formatPricelistOriginLabel } from "@entintados_pdv/app/utils/pricing_rules";
import { formatOriginBadge } from "@entintados_pdv/app/utils/origin_badge";
import { admitsLinePromotion } from "@entintados_pdv/app/utils/promotion_rules";

// Más avisos que estos se agrupan en uno solo, como en Ventas.
const PROMOTION_NOTIFICATION_THRESHOLD = 5;

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

    // El core la llama dentro de `updateRewards` tras cada cambio del pedido: producto,
    // teclado, cliente, tarifa o código de barras.
    async orderUpdateLoyaltyPrograms() {
        await super.orderUpdateLoyaltyPrograms(...arguments);
        this._refreshLinePromotions(this.getOrder());
    },

    async pay() {
        this._refreshLinePromotions(this.getOrder());
        return super.pay(...arguments);
    },

    /**
     * Descuento tecleado: con promoción de pedido activa no se cambia, como en Ventas. En una
     * línea en promoción la sustituye y queda manual; si la caja rechaza el valor (precio fijo o
     * sin permiso), la promoción se conserva.
     */
    async setDiscountFromUI(line, val) {
        const order = line.order_id;
        const orderReward = order?.order_promo_reward_id;
        if (orderReward && admitsLinePromotion(line)) {
            this.notification.add(
                _t(
                    "El pedido tiene la promoción %s en todas sus líneas. Quítala del pedido para cambiar el descuento.",
                    orderReward.program_id?.name || ""
                ),
                { type: "warning" }
            );
            return;
        }
        if (!line.promo_reward_id) {
            return super.setDiscountFromUI(...arguments);
        }
        const previous = {
            promo_reward_id: line.promo_reward_id,
            pricing_rule_type: line.pricing_rule_type,
            pricing_rule_origin: line.pricing_rule_origin,
            discount: line.discount,
        };
        const discount = Number(val) || 0;
        line.promo_reward_id = false;
        line.pricing_rule_type = "manual";
        line.pricing_rule_origin = formatOriginBadge(_t("Manual"), discount);
        const result = await super.setDiscountFromUI(...arguments);
        if (line.discount === previous.discount && discount !== previous.discount) {
            Object.assign(line, previous);
        }
        return result;
    },

    _refreshLinePromotions(order) {
        if (!order || order.finalized) {
            return;
        }
        this._notifyPromotionChanges(order.reevaluateLinePromotions());
    },

    _notifyPromotionChanges(notices) {
        if (!notices.length) {
            return;
        }
        if (notices.length > PROMOTION_NOTIFICATION_THRESHOLD) {
            this.notification.add(
                _t("Se actualizaron o retiraron promociones en %s líneas del pedido.", notices.length),
                { title: _t("Promociones"), type: "warning" }
            );
            return;
        }
        for (const notice of notices) {
            this.notification.add(notice.message, { title: notice.title, type: notice.type });
        }
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
                line.price_origin_label = "Modificado de forma manual";
                continue;
            }
            if (line.is_reward_line) {
                continue;
            }
            if (line.is_tint_colorant || line.product_id?.tint_role === "colorant") {
                line.pricing_rule_type = "none";
                line.pricing_rule_origin = "";
                line.pricing_rule_id = false;
                line.price_origin = "tint";
                line.price_origin_label = "Precio del entintado: base más colorantes";
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
                    line.pricing_rule_origin = rule.originLabel || "Precio fijo";
                    line.pricing_rule_id = rule.rule?.id || false;
                    line.promo_reward_id = false;
                    line.price_origin = line.is_tinted_base ? "tint" : (rule.priceOrigin || "fixed_price");
                    line.price_origin_label = line.is_tinted_base
                        ? "Precio del entintado: base más colorantes"
                        : (rule.originLabel || "Precio fijo");
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
                const pricelist = this.pricelist_id || this.pricelist || this.config?.pricelist_id || posStoreInstance?.pricelist || (models?.["product.pricelist"]?.getFirst?.()) || (models?.["product.pricelist"]?.getAll?.()?.[0]);
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

            // La promoción de la línea la decide el motor de promociones: aquí solo el acuerdo.
            if (line.promo_reward_id) {
                continue;
            }

            const agreementDiscount = rule.type === "discount" ? Number(rule.discount || 0) : 0;

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
