/** @odoo-module */
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

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
});

patch(PosOrder.prototype, {
    _updateRewardLines(...args) {
        const beforeKeys = new Set(
            (this.lines || []).filter((l) => l.is_reward_line).map((l) => l.reward_identifier_code)
        );

        const result = super._updateRewardLines(...args);

        const afterLines = (this.lines || []).filter((l) => l.is_reward_line);
        const partnerDiscount = Number(this.partner_id?.discount || 0) * 100;

        for (const line of afterLines) {
            if (beforeKeys.has(line.reward_identifier_code)) {
                continue;
            }
            this._entintadosHandleNewReward(line, partnerDiscount);
        }

        this._entintadosReconcileDiscounts(partnerDiscount);

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
            // Eliminación manual del cajero: olvidamos la decisión previa
            // para que el descuento regrese y se vuelva a preguntar si
            // el producto vuelve a calificar más adelante.
            if (this.uiState?.promoDecisions) {
                delete this.uiState.promoDecisions[decisionKey];
            }
        }

        const partnerDiscount = Number(this.partner_id?.discount || 0) * 100;
        this._entintadosReconcileDiscounts(partnerDiscount);

        return result;
    },

    async _entintadosHandleNewReward(line, partnerDiscount) {
        const program = line.reward_id?.program_id;
        const isAutomaticPromotion =
            program?.program_type === "promotion" && program?.trigger === "auto";

        if (!isAutomaticPromotion || !posStoreInstance || partnerDiscount <= 0) {
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
                body: `Este producto califica para la promoción "${program?.name}". El cliente tiene ${partnerDiscount}% de descuento asignado. Elegir la promoción quitará el descuento de TODA la orden. ¿Qué deseas aplicar?`,
                confirmLabel: "Aplicar promoción",
                cancelLabel: `Mantener descuento (${partnerDiscount}%)`,
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

        this._entintadosReconcileDiscounts(partnerDiscount);
    },

    _entintadosReconcileDiscounts(partnerDiscount) {
        const hasAcceptedPromo = Object.values(this.uiState?.promoDecisions || {}).includes("accepted");

        for (const line of this.lines || []) {
            if (line.is_reward_line || line.price_type === "manual" || line.manual_price) {
                continue;
            }
            line.discount = (partnerDiscount > 0 && !hasAcceptedPromo) ? partnerDiscount : 0;
        }
    },
});