import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosStore } from "@point_of_sale/app/services/pos_store";

patch(PosStore.prototype, {
    async refreshTotalDueOfPartner(partner) {
        const updates = await super.refreshTotalDueOfPartner(...arguments);
        for (const values of updates || []) {
            const local = this.models["res.partner"].get(values.id);
            if (local && values.customer_advance_available !== undefined) {
                local.customer_advance_available = values.customer_advance_available;
            }
        }
        return updates;
    },
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
        const order = this.getOrder();
        const incomingAdvance = !values.advance_source_ref && Number(values.price_unit ?? 0) >= 0 &&
            (values.product_id?.id === this.config.down_payment_product_id?.id ||
             values.product_tmpl_id?.is_accounting_advance);
        const receivingAdvance = order?.lines.some((line) =>
            line.isAdvanceLocked && !line.advance_source_ref && line.price_unit * line.qty > 0
        );
        if (receivingAdvance || (incomingAdvance && order?.lines.length)) {
            this.notification.add(_t("La recepción de un anticipo debe contener únicamente el anticipo. Usa otra orden para agregar productos."), { type: "warning" });
            return;
        }
        const line = await super.addLineToCurrentOrder(...arguments);
        if (line && this.config.advance_risk_enabled &&
            values.product_id?.id === this.config.down_payment_product_id?.id) {
            line.order_id.setToInvoice(true);
        }
        return line;
    },
});
