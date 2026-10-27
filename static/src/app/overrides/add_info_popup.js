import { patch } from "@web/core/utils/patch";
import { AddInfoPopup } from "@l10n_mx_edi_pos/app/components/popups/add_info_popup/add_info_popup";

patch(AddInfoPopup.prototype, {
    get canSplitInvoice() {
        const order = this.props.order;
        return !order.uiState?.creditInvoiceSetup && !['credit', 'ppd_partial'].includes(order.pos_payment_flow) &&
            order.l10n_mx_edi_payment_policy !== 'PPD' &&
            !order.payment_ids?.some((line) => line.amount > 0 && line.payment_method_id?.type === 'pay_later');
    },
    setup() {
        super.setup(...arguments);
        const order = this.props.order;
        this.state.invoice_split_count = this.canSplitInvoice
            ? order.uiState?.invoice_split_count || order.invoice_split_count || 1 : 1;
    },
    confirm() {
        const n = this.canSplitInvoice ? Math.max(1, parseInt(this.state.invoice_split_count) || 1) : 1;
        this.state.invoice_split_count = n;
        const order = this.props.order;
        if (order.uiState) {
            order.uiState.invoice_split_count = n;
        }
        order.invoice_split_count = n;
        return super.confirm(...arguments);
    },
});
