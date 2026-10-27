import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { patch } from "@web/core/utils/patch";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { ask } from "@point_of_sale/app/utils/make_awaitable_dialog";

patch(OrderPaymentValidation.prototype, {
    async settleOrderDues(partner, paylaterPaymentMethod, settleLines) {
        const invoiceLines = settleLines.filter((line) =>
            line.settled_invoice_id || line.settled_order_id?.account_move);
        if (!invoiceLines.length) {
            return await super.settleOrderDues(...arguments);
        }
        const order = this.order;
        if (invoiceLines.length !== order.lines.length) {
            this.pos.dialog.add(AlertDialog, {
                title: _t("Cobro de facturas"),
                body: _t("Cobra las facturas en un ticket separado de productos, anticipos u otras deudas."),
            });
            return false;
        }
        const received = order.payment_ids.filter((line) => line.payment_method_id.type !== "pay_later")
            .reduce((total, line) => total + line.amount, 0);
        let remaining = Math.min(received, order.getSettleAmount());
        if (remaining <= 0) {
            this.pos.dialog.add(AlertDialog, {
                title: _t("Cobro de facturas"), body: _t("Registra un importe de pago mayor que cero."),
            });
            return false;
        }
        const originalPrices = invoiceLines.map((line) => line.price_unit);
        for (const line of invoiceLines) {
            const amount = Math.min(remaining, line.prices.total_included);
            line.setUnitPrice(amount);
            remaining = Math.max(0, remaining - amount);
        }
        order.setToInvoice(false);
        order.pos_payment_flow = "standard";
        try {
            if (!order.commercialPartnerId || order.commercialPartnerId !== partner.commercial_partner_id?.id) {
                this.pos.dialog.add(AlertDialog, {
                    title: _t("Cobro de facturas"),
                    body: _t("Las facturas seleccionadas deben corresponder al cliente del ticket."),
                });
                return false;
            }
            const amount = order.getSettleAmount();
            const names = [...new Set(invoiceLines.filter((line) => line.price_unit > 0).map((line) =>
                line.settled_invoice_id?.name || line.settled_order_id?.account_move?.name ||
                line.full_product_name || _t("seleccionada")))];
            const formatted = this.pos.env.utils.formatCurrency(amount);
            const confirmed = await ask(this.pos.dialog, {
                title: _t("Confirmar abono"),
                body: names.length > 1
                    ? _t("¿Desea abonar %s a las facturas %s del cliente %s?", formatted, names.join(", "), partner.name)
                    : _t("¿Desea abonar %s a la factura %s del cliente %s?", formatted, names[0], partner.name),
                confirmLabel: _t("Sí, abonar"), cancelLabel: _t("Cancelar"),
            });
            if (!confirmed) return false;
            const result = order.addPaymentline(paylaterPaymentMethod);
            if (!result.status) return false;
            result.data.setAmount(-amount);
            settleLines.forEach((line) => { line.qty = 0; });
            return await super.validateOrder();
        } finally {
            // Cancelling the native confirmation must not shrink the selection.
            if (!order.payment_ids.some((line) => line.payment_method_id.type === "pay_later")) {
                invoiceLines.forEach((line, index) => line.setUnitPrice(originalPrices[index]));
            }
        }
    },
});
