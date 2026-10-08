import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";

patch(PosStore.prototype, {
    requireCustomer(order = this.getOrder()) {
        if (order?.getPartner?.() || order?.partner_id) {
            return true;
        }
        this.notification.add(_t("Selecciona primero un cliente para iniciar la compra."),
            { type: "warning" });
        return false;
    },

    async addLineToOrder(vals, order, ...args) {
        if (!this.requireCustomer(order)) {
            return;
        }
        return super.addLineToOrder(vals, order, ...args);
    },

    async pay(...args) {
        if (!this.requireCustomer()) {
            return;
        }
        return super.pay(...args);
    },
});

patch(OrderPaymentValidation.prototype, {
    async validateOrder(...args) {
        if (!this.pos.requireCustomer(this.order)) {
            return false;
        }
        return super.validateOrder(...args);
    },
});
