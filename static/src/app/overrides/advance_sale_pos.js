import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosStore } from "@point_of_sale/app/services/pos_store";

patch(PosStore.prototype, {
    async addDownPaymentProductOrderlineToOrder(saleOrder, amount, isPercentage) {
        if (!Number.isFinite(amount) || amount === 0 ||
            (amount > 0 && (isPercentage ? amount > 100 : amount > saleOrder.amount_unpaid))) {
            this.notification.add(_t("El anticipo debe ser positivo y no superar el saldo pendiente del pedido (máximo 100%)."), { type: "warning" });
            return;
        }
        // Negative amounts are also used by native settleSO to deduct prior payments.
        if (amount < 0 && isPercentage) {
            return;
        }
        return await super.addDownPaymentProductOrderlineToOrder(...arguments);
    },
    async addLineToCurrentOrder(values, options = {}, configure = true) {
        const line = await super.addLineToCurrentOrder(...arguments);
        if (line && this.config.advance_risk_enabled && values.sale_order_origin_id &&
            values.product_id?.id === this.config.down_payment_product_id?.id) {
            line.order_id.setToInvoice(true);
        }
        return line;
    },
});
