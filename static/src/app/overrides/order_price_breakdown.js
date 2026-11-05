import { patch } from "@web/core/utils/patch";
import { OrderDisplay } from "@point_of_sale/app/components/order_display/order_display";
import { customerSummary } from "../utils/customer_summary";

patch(OrderDisplay.prototype, {
    get orderPriceBreakdown() {
        return customerSummary(this.order);
    },
});
