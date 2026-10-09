import { patch } from "@web/core/utils/patch";
import { AddInfoPopup } from "@l10n_mx_edi_pos/app/components/popups/add_info_popup/add_info_popup";

patch(AddInfoPopup.prototype, {
    setup() {
        super.setup(...arguments);
        const order = this.props.order;
        this.state.invoice_split_count =
            order.uiState?.invoice_split_count || order.invoice_split_count || 1;
    },
    confirm() {
        const n = Math.max(1, parseInt(this.state.invoice_split_count) || 1);
        const order = this.props.order;
        if (order.uiState) {
            order.uiState.invoice_split_count = n;
        }
        order.invoice_split_count = n;
        return super.confirm(...arguments);
    },
});