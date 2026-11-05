export function printableQuotations(order, lastCreated) {
    const quotations = new Map();
    for (const line of order?.lines || []) {
        const quotation = line.sale_order_origin_id || line.sale_order_line_id?.order_id;
        if (Number.isInteger(quotation?.id) && quotation.id > 0) {
            quotations.set(quotation.id, quotation);
        }
    }
    if (!quotations.size && Number.isInteger(lastCreated?.id) && lastCreated.id > 0) {
        quotations.set(lastCreated.id, lastCreated);
    }
    return [...quotations.values()];
}
