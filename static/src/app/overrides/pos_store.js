/** @odoo-module */
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { _t } from "@web/core/l10n/translation";
import { formatOriginBadge } from "@entintados_pdv/app/utils/origin_badge";
import { admitsLinePromotion } from "@entintados_pdv/app/utils/promotion_rules";
import { agreementForLine } from "@entintados_pdv/app/utils/pricing_rules";
import { isTintColorant, isTintedBase } from "@entintados_pdv/app/utils/tint_order";
import { isComboItem, isComboPackage } from "@entintados_pdv/app/utils/combo_rules";

// Más avisos que estos se agrupan en uno solo, como en Ventas.
const PROMOTION_NOTIFICATION_THRESHOLD = 5;

// Mitad del último decimal de la precisión `Discount`.
const DISCOUNT_TOLERANCE = 0.005;

patch(PosStore.prototype, {
    editPartnerContext(partner) {
        return {
            ...super.editPartnerContext(partner),
            default_is_customer: true,
            entintados_partner_scope: "customer",
        };
    },

    get canEditUnitPrice() {
        return Boolean(this.config?.user_can_edit_price ?? true);
    },

    get canEditDiscount() {
        return Boolean(this.config?.user_can_edit_discount ?? true);
    },

    async addLineToCurrentOrder(...args) {
        const line = await super.addLineToCurrentOrder(...args);
        (line?.order_id || this.getOrder())?.refreshOrderPricing();
        return line;
    },

    async addLineToOrder(...args) {
        const line = await super.addLineToOrder(...args);
        (line?.order_id || this.getOrder())?.refreshOrderPricing();
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
     * Descuento tecleado, con las reglas de Ventas:
     * 1. con promoción de pedido activa no se cambia;
     * 2. en un producto de combo o en un entintado no se teclea: lo dan su paquete, su
     *    acuerdo o su promoción;
     * 3. el mismo % del acuerdo vigente, en una línea sin promoción, conserva el acuerdo;
     * 4. cualquier otro valor deja la línea manual y le quita la promoción.
     * En un paquete de combo, sus productos copian el resultado.
     * Si la caja rechaza el valor (precio fijo o sin permiso), la línea queda como estaba.
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
        if (isComboItem(line)) {
            this.notification.add(
                _t(
                    "No se puede cambiar el descuento de «%(product)s»: es parte del combo «%(package)s». El descuento se pone en el paquete.",
                    {
                        product: line.product_id?.display_name || "",
                        package: line.combo_parent_id?.product_id?.display_name || "",
                    }
                ),
                { type: "warning" }
            );
            return;
        }
        if (isTintedBase(line) || isTintColorant(line)) {
            this.notification.add(
                _t("El descuento de un entintado no se teclea: lo da su acuerdo o su promoción."),
                { type: "warning" }
            );
            return;
        }
        const previousDiscount = line.discount;
        const hadPromotion = Boolean(line.promo_reward_id);
        const requested = Number(val) || 0;
        const result = await super.setDiscountFromUI(...arguments);
        const rejected = line.discount === previousDiscount && requested !== previousDiscount;
        if (rejected || line.fixed_price_locked) {
            // El core copia el descuento a los productos del combo antes que al paquete.
            if (isComboPackage(line)) {
                order.syncComboItems(line);
            }
            return result;
        }
        const agreement = agreementForLine(order, line);
        const agreementDiscount = agreement.type === "discount" ? Number(agreement.discount || 0) : 0;
        const keepsAgreement =
            !hadPromotion &&
            agreement.type !== "fixed_price" &&
            Math.abs(Number(line.discount || 0) - agreementDiscount) < DISCOUNT_TOLERANCE;
        line.promo_reward_id = false;
        if (keepsAgreement && agreement.type === "discount") {
            line.pricing_rule_type = agreement.originType;
            line.pricing_rule_origin = agreement.originLabel;
            line.pricing_rule_id = agreement.rule || false;
        } else if (keepsAgreement) {
            line.pricing_rule_type = "none";
            line.pricing_rule_origin = "";
            line.pricing_rule_id = false;
        } else {
            line.pricing_rule_type = "manual";
            line.pricing_rule_origin = formatOriginBadge(_t("Manual"), line.discount);
            line.pricing_rule_id = false;
        }
        if (isComboPackage(line)) {
            order.syncComboItems(line);
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
