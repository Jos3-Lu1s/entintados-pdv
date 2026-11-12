export function canSplitInvoice(order) {
    return !!order && !["credit", "ppd_partial"].includes(order.pos_payment_flow) &&
        order.l10n_mx_edi_payment_policy !== "PPD" &&
        !order.payment_ids?.some((line) => line.amount > 0 && line.payment_method_id?.type === "pay_later");
}

export function setInvoiceSplitCount(order, value) {
    const count = canSplitInvoice(order) ? Math.max(1, parseInt(value) || 1) : 1;
    if (order.uiState) {
        order.uiState.invoice_split_count = count;
    }
    order.invoice_split_count = count;
    return count;
}

export function isInvoiceCollection(order) {
    return !!order?.lines?.some((line) => line.settled_invoice_id || line.settled_order_id?.account_move);
}

export function initializePaymentInvoice(order, isMexico) {
    if (!order || order.finalized || ["paid", "done", "cancel"].includes(order.state)) {
        return;
    }
    order.uiState ||= {};
    if (isInvoiceCollection(order)) {
        return;
    }
    order.setToInvoice(true);
    if (order.uiState.paymentInvoiceDefaultApplied) {
        return;
    }
    order.uiState.paymentInvoiceDefaultApplied = true;
    if (isMexico) {
        order.l10n_mx_edi_usage ||= order.getPartner()?.l10n_mx_edi_usage || "G01";
        order.l10n_mx_edi_cfdi_to_public ??= false;
    }
}
