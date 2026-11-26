import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";

patch(ReceiptScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.invoiceReportAction = useService("action");
    },

    get receiptInvoiceIds() {
        const raw = this.currentOrder?.raw;
        return [...new Set([raw?.account_move, ...(raw?.split_invoice_ids || [])])]
            .filter((id) => Number.isInteger(id) && id > 0);
    },

    get receiptInvoicePreviewUrl() {
        const reportUrl = "/report/pdf/entintados_pdv.report_invoice_live/" + this.receiptInvoiceIds.join(",");
        return "/web/static/lib/pdfjs/web/viewer.html?file=" + encodeURIComponent(reportUrl);
    },

    downloadReceiptInvoice() {
        const invoiceIds = this.receiptInvoiceIds;
        if (invoiceIds.length) {
            return this.invoiceReportAction.doAction("entintados_pdv.action_report_invoice_live", {
                additionalContext: { active_model: "account.move", active_ids: invoiceIds, active_id: invoiceIds[0] },
            });
        }
    },
});
