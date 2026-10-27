import { patch } from "@web/core/utils/patch";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";

patch(PosOrderline.prototype, {
    get isDebtCollectionLine() {
        return Boolean(this.settled_invoice_id || this.settled_order_id);
    },
    get canRemoveDebtCollection() {
        const order = this.order_id;
        return Boolean(this.isDebtCollectionLine && order && !order.finalized &&
            (!order.state || order.state === "draft") && !order.invoice_collection_processed &&
            !order.electronicPaymentInProgress?.());
    },
});

patch(PosOrder.prototype, {
    removeOrderline(line) {
        if (!line?.isDebtCollectionLine) return super.removeOrderline(...arguments);
        if (!line.canRemoveDebtCollection) return false;
        const result = super.removeOrderline(...arguments);
        if (this.lines.includes(line)) return result;
        // The native validator may have zeroed the collection lines and added
        // one negative customer-account entry. Rebuild that entry on validation.
        for (const payment of [...this.payment_ids]) {
            if (payment.payment_method_id.type === "pay_later" && payment.amount < 0) payment.delete();
        }
        for (const remaining of this.lines) {
            if (remaining.isDebtCollectionLine && remaining.qty === 0) remaining.qty = 1;
        }
        this.pos_payment_flow = "standard";
        if (!this.lines.length) this.is_settling_account = false;
        return result;
    },
});

patch(OrderSummary.prototype, {
    async updateSelectedOrderline(event) {
        const line = this.currentOrder.getSelectedOrderline();
        if (line?.isDebtCollectionLine && event?.key === "Backspace") {
            this.numberBuffer.reset();
            return this.currentOrder.removeOrderline(line);
        }
        return await super.updateSelectedOrderline(...arguments);
    },
    _setValue(value) {
        const line = this.currentOrder.getSelectedOrderline();
        if (line?.isDebtCollectionLine && value === "remove") {
            this.numberBuffer.reset();
            return this.currentOrder.removeOrderline(line);
        }
        return super._setValue(...arguments);
    },
});
