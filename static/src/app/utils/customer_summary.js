/** Customer amounts are taken from Odoo's tax engine, always excluding tax. */
export function customerSummary(order, showMargins = false) {
    const lines = order.lines || [];
    let discounts = 0;
    let promotions = 0;
    for (const line of lines) {
        const saving = line.priceExclNoDiscount - line.priceExcl;
        if (line.is_reward_line && line.priceExcl < 0) {
            promotions -= line.priceExcl;
        } else if (line.pricing_rule_type === "promo") {
            promotions += saving;
        } else {
            discounts += saving;
        }
    }
    const rows = lines.filter((line) => !line.combo_parent_id &&
        !(line.is_reward_line && line.priceExcl < 0)).map((line) => {
        const components = [line, ...(line.combo_line_ids || [])];
        const amount = components.reduce((sum, item) => sum + item.priceExcl, 0);
        const gross = components.reduce((sum, item) => sum + item.priceExclNoDiscount, 0);
        const cost = showMargins ? components.reduce((sum, item) => sum + item.getTotalCost(), 0) : NaN;
        const margin = Number.isFinite(cost) ? amount - cost : null;
        return {
            id: line.uuid,
            name: line.getFullProductName?.() || line.product_id?.display_name || line.product_id?.name || "",
            color: line.tint_color_code || "",
            quantity: line.qty,
            unit: line.product_id?.uom_id?.name || "",
            unitPrice: line.qty ? gross / line.qty : 0,
            saving: gross - amount,
            amount,
            margin,
            marginPercent: margin !== null && amount !== 0 ? (margin / amount) * 100 : null,
        };
    });
    const taxes = (order.prices.taxDetails.subtotals || []).flatMap((subtotal) =>
        (subtotal.tax_groups || []).map((group) => ({
            name: group.group_name,
            amount: group.tax_amount_currency,
        })));
    return {
        rows, discounts, promotions, taxes, showMargins,
        gross: order.priceExcl + discounts + promotions,
        subtotal: order.priceExcl,
        tax: order.amountTaxes,
        rounding: order.totalDue - order.priceIncl,
        total: order.totalDue,
    };
}
