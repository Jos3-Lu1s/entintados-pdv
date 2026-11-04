import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { ProductInfoPopup } from "@point_of_sale/app/components/popups/product_info_popup/product_info_popup";
import { customerSummary } from "../utils/customer_summary";

patch(PaymentScreen.prototype, {
    get customerPaymentSummary() {
        const showMargins = ProductInfoPopup.prototype._hasMarginsCostsAccessRights.call(this);
        return customerSummary(this.currentOrder, showMargins);
    },
});
