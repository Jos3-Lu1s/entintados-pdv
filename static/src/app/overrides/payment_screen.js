import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { PaymentScreenStatus } from "@point_of_sale/app/screens/payment_screen/payment_status/payment_status";
import { CustomerPaymentSummary } from "@entintados_pdv/app/components/customer_payment_summary/customer_payment_summary";
import { isInvoiceCollection, initializePaymentInvoice } from "@entintados_pdv/app/utils/invoice_options";

patch(PaymentScreen, {
    components: { ...PaymentScreen.components, CustomerPaymentSummary },
});
if (PaymentScreenStatus) {
    patch(PaymentScreenStatus, {
        components: { ...PaymentScreenStatus.components, CustomerPaymentSummary },
    });
}

patch(PaymentScreen.prototype, {
    async toggleIsToInvoice() {
        if (isInvoiceCollection(this.currentOrder)) {
            return await super.toggleIsToInvoice(...arguments);
        }
        initializePaymentInvoice(this.currentOrder, this.pos?.company?.country_id?.code === "MX");
    },
});
