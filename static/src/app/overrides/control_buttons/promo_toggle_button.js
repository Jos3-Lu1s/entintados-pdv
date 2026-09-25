/** @odoo-module */
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { getPartnerPricingRule } from "@entintados_pdv/app/utils/pricing_rules";
import { PromoTogglePopup } from "@entintados_pdv/app/overrides/control_buttons/promo_toggle_popup";

patch(ControlButtons.prototype, {
    /**
     * Determina si el botón de Promociones / Acuerdos debe mostrarse:
     * - La orden califica para al menos una promoción elegible o activa.
     * - O el cliente seleccionado tiene acuerdos comerciales de descuento.
     */
    get isPromoToggleVisible() {
        const order = this.pos.getOrder?.() || this.pos.currentOrder;
        if (!order) return false;

        const hasActivePromo = Boolean(order.uiState?.activePromoReward);
        const hasEligiblePromo =
            typeof order._entintadosGetEligiblePromoReward === "function" &&
            Boolean(order._entintadosGetEligiblePromoReward());

        const partner = order.getPartner?.() || order.get_partner?.() || order.partner_id;
        const models = this.pos.models;
        const hasCollidingDiscount =
            Boolean(partner) &&
            (order.lines || []).some((l) => {
                if (l.is_reward_line || l.is_tint_colorant || l.product_id?.tint_role === "colorant" || l.fixed_price_locked) {
                    return false;
                }
                const r = getPartnerPricingRule(models, partner, l.product_id);
                return r.type === "discount" && r.discount > 0;
            });

        return hasActivePromo || hasEligiblePromo || hasCollidingDiscount;
    },

    /**
     * Describe el modo de tarificación activo en la orden para etiquetas o badges.
     */
    get currentPricingModeLabel() {
        const order = this.pos.getOrder?.() || this.pos.currentOrder;
        if (!order) return _t("Promociones / Acuerdos");

        if (order.uiState?.pricingModePreference === "promo" || order.uiState?.activePromoReward) {
            const reward =
                order.uiState.activePromoReward ||
                (typeof order._entintadosGetEligiblePromoReward === "function"
                    ? order._entintadosGetEligiblePromoReward()
                    : null);
            const promoName = reward?.program_id?.name || reward?.description;
            return promoName ? _t("Promoción: %s", promoName) : _t("Promoción");
        }

        if (order.uiState?.pricingModePreference === "commercial_agreements") {
            return _t("Acuerdos del Cliente");
        }

        const partner = order.getPartner?.() || order.get_partner?.() || order.partner_id;
        const models = this.pos.models;
        const hasCollidingDiscount =
            Boolean(partner) &&
            (order.lines || []).some((l) => {
                if (l.is_reward_line || l.is_tint_colorant || l.product_id?.tint_role === "colorant" || l.fixed_price_locked) {
                    return false;
                }
                const r = getPartnerPricingRule(models, partner, l.product_id);
                return r.type === "discount" && r.discount > 0;
            });

        if (hasCollidingDiscount) {
            return _t("Acuerdos del Cliente");
        }

        return _t("Promociones / Acuerdos");
    },

    get isPromoActive() {
        const order = this.pos.getOrder?.() || this.pos.currentOrder;
        return Boolean(order?.uiState?.activePromoReward);
    },

    get isCommercialAgreementsActive() {
        const order = this.pos.getOrder?.() || this.pos.currentOrder;
        return order?.uiState?.pricingModePreference === "commercial_agreements";
    },

    /**
     * Acción ejecutada al hacer clic en el botón de alternancia.
     * Despliega el popup interactivo de selección o notificaciones informativas.
     */
    async onClickPromoToggle() {
        const order = this.pos.getOrder?.() || this.pos.currentOrder;
        if (!order) return;

        const partner = order.getPartner?.() || order.get_partner?.() || order.partner_id;
        const models = this.pos.models;
        const eligiblePromo =
            order.uiState?.activePromoReward ||
            (typeof order._entintadosGetEligiblePromoReward === "function"
                ? order._entintadosGetEligiblePromoReward()
                : null);

        const hasCollidingDiscount =
            Boolean(partner) &&
            (order.lines || []).some((l) => {
                if (l.is_reward_line || l.is_tint_colorant || l.product_id?.tint_role === "colorant" || l.fixed_price_locked) {
                    return false;
                }
                const r = getPartnerPricingRule(models, partner, l.product_id);
                return r.type === "discount" && r.discount > 0;
            });

        // 1. Si no califica a promoción y el cliente no tiene acuerdos comerciales
        if (!eligiblePromo && !hasCollidingDiscount) {
            this.notification.add(
                _t("No hay promociones elegibles ni acuerdos comerciales aplicables en este pedido."),
                { type: "info" }
            );
            return;
        }

        // 2. Si solo aplica promoción (sin acuerdos de cliente colisionantes)
        if (eligiblePromo && !hasCollidingDiscount) {
            if (order.uiState?.activePromoReward) {
                this.notification.add(
                    _t("La promoción «%s» ya está activa en la orden y el cliente no cuenta con acuerdos de descuento.",
                        eligiblePromo.program_id?.name || eligiblePromo.description
                    ),
                    { type: "info" }
                );
            } else {
                order.uiState.pricingModePreference = "promo";
                order.uiState.activePromoReward = eligiblePromo;
                if (!order.uiState.promoDecisions) order.uiState.promoDecisions = {};
                const decisionKey = partner?.id ? `${eligiblePromo.id}_${partner.id}` : String(eligiblePromo.id);
                order.uiState.promoDecisions[decisionKey] = "accepted";
                if (order.uiState.disabledRewards) {
                    order.uiState.disabledRewards.delete(eligiblePromo.id);
                }
                order._entintadosApplyInlinePromoDiscount(eligiblePromo);
                this.notification.add(
                    _t("Se aplicó la promoción «%s» a la orden.",
                        eligiblePromo.program_id?.name || eligiblePromo.description
                    ),
                    { type: "success" }
                );
            }
            return;
        }

        // 3. Si solo aplican acuerdos comerciales (sin promoción elegible)
        if (!eligiblePromo && hasCollidingDiscount) {
            order.uiState.pricingModePreference = "commercial_agreements";
            order.uiState.activePromoReward = null;
            order._entintadosReconcileDiscounts();
            this.notification.add(
                _t("Rigen los acuerdos comerciales del cliente. El pedido no califica para promociones actualmente."),
                { type: "info" }
            );
            return;
        }

        // 4. Si ambos aplican: desplegar modal interactivo de alternancia
        const currentMode = order.uiState?.activePromoReward
            ? "promo"
            : (order.uiState?.pricingModePreference || "commercial_agreements");

        this.dialog.add(PromoTogglePopup, {
            promoReward: eligiblePromo,
            partner: partner,
            currentMode: currentMode,
            onSelect: (selectedMode) => {
                if (selectedMode === "commercial_agreements") {
                    order.uiState.pricingModePreference = "commercial_agreements";
                    order.uiState.activePromoReward = null;
                    if (!order.uiState.promoDecisions) order.uiState.promoDecisions = {};
                    const decisionKey = partner?.id ? `${eligiblePromo.id}_${partner.id}` : String(eligiblePromo.id);
                    order.uiState.promoDecisions[decisionKey] = "declined";
                    if (!order.uiState.disabledRewards) order.uiState.disabledRewards = new Set();
                    order.uiState.disabledRewards.add(eligiblePromo.id);
                    order._entintadosReconcileDiscounts();
                    this.notification.add(
                        _t("Se aplicaron los acuerdos comerciales del cliente."),
                        { type: "success" }
                    );
                } else if (selectedMode === "promo") {
                    order.uiState.pricingModePreference = "promo";
                    order.uiState.activePromoReward = eligiblePromo;
                    if (!order.uiState.promoDecisions) order.uiState.promoDecisions = {};
                    const decisionKey = partner?.id ? `${eligiblePromo.id}_${partner.id}` : String(eligiblePromo.id);
                    order.uiState.promoDecisions[decisionKey] = "accepted";
                    if (order.uiState.disabledRewards) {
                        order.uiState.disabledRewards.delete(eligiblePromo.id);
                    }
                    order._entintadosApplyInlinePromoDiscount(eligiblePromo);
                    this.notification.add(
                        _t("Se aplicó la promoción «%s».",
                            eligiblePromo.program_id?.name || eligiblePromo.description
                        ),
                        { type: "success" }
                    );
                }
            },
        });
    },
});
