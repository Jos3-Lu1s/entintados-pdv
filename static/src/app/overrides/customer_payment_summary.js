import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { customerSummary } from "../utils/customer_summary";

patch(PaymentScreen.prototype, {
    get customerPaymentSummary() {
        return customerSummary(this.currentOrder);
    },
});
