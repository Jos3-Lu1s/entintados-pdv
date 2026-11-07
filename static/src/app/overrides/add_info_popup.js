import { patch } from "@web/core/utils/patch";
import { AddInfoPopup } from "@l10n_mx_edi_pos/app/components/popups/add_info_popup/add_info_popup";
import { canSplitInvoice, setInvoiceSplitCount } from "../utils/invoice_options";

patch(AddInfoPopup.prototype, {
    get canSplitInvoice() {
        const order = this.props.order;
        return canSplitInvoice(order);
    },
    setup() {
        super.setup(...arguments);
        const order = this.props.order;
        this.state.invoice_split_count = this.canSplitInvoice
            ? order.uiState?.invoice_split_count || order.invoice_split_count || 1 : 1;
    },
    confirm() {
        const n = setInvoiceSplitCount(this.props.order, this.state.invoice_split_count);
        this.state.invoice_split_count = n;
        return super.confirm(...arguments);
    },
});
