import { patch } from "@web/core/utils/patch";
import { useEffect } from "@odoo/owl";
import { formatFloat } from "@web/views/fields/formatters";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { ProductInfoPopup } from "@point_of_sale/app/components/popups/product_info_popup/product_info_popup";
import { customerSummary } from "../utils/customer_summary";
import { canSplitInvoice, initializePaymentInvoice, isInvoiceCollection, setInvoiceSplitCount } from "../utils/invoice_options";

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        useEffect(() => {
            initializePaymentInvoice(this.currentOrder, this.pos.company.country_id?.code === "MX");
        }, () => [this.currentOrder?.uuid, this.currentOrder?.to_invoice]);
    },
    onMounted() {
        if (!this.currentOrder) {
            return;
        }
        const result = super.onMounted(...arguments);
        initializePaymentInvoice(this.currentOrder, this.pos.company.country_id?.code === "MX");
        return result;
    },
    async toggleIsToInvoice() {
        if (isInvoiceCollection(this.currentOrder)) {
            return await super.toggleIsToInvoice(...arguments);
        }
        initializePaymentInvoice(this.currentOrder, this.pos.company.country_id?.code === "MX");
    },
    get showPaymentInvoiceOptions() {
        return !!this.currentOrder && this.pos.company.country_id?.code === "MX" &&
            !isInvoiceCollection(this.currentOrder);
    },
    get paymentInvoiceUsage() {
        return this.currentOrder.l10n_mx_edi_usage || this.currentOrder.getPartner()?.l10n_mx_edi_usage || "G01";
    },
    get canSplitPaymentInvoice() {
        return canSplitInvoice(this.currentOrder);
    },
    get paymentInvoiceSplitCount() {
        return this.canSplitPaymentInvoice
            ? this.currentOrder.uiState?.invoice_split_count || this.currentOrder.invoice_split_count || 1 : 1;
    },
    changePaymentInvoiceUsage(event) {
        this.currentOrder.l10n_mx_edi_usage = event.target.value;
    },
    changePaymentInvoicePublic(event) {
        this.currentOrder.l10n_mx_edi_cfdi_to_public = event.target.value === "1";
    },
    changePaymentInvoiceSplitCount(event) {
        event.target.value = String(setInvoiceSplitCount(this.currentOrder, event.target.value));
    },
    formatMarginPercent(percent) {
        return formatFloat(percent, { digits: [16, 2] });
    },
    get customerPaymentSummary() {
        const showMargins = ProductInfoPopup.prototype._hasMarginsCostsAccessRights.call(this);
        return customerSummary(this.currentOrder, showMargins);
    },
});
