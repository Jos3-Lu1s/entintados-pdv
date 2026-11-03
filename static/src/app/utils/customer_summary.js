/** Customer amounts are taken from Odoo's tax engine, always excluding tax. */
export function customerSummary(order) {
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
        return {
            id: line.uuid,
            name: line.getFullProductName?.() || line.product_id?.display_name || line.product_id?.name || "",
            color: line.tint_color_code || "",
            quantity: line.qty,
            unit: line.product_id?.uom_id?.name || "",
            unitPrice: line.qty ? gross / line.qty : 0,
            saving: gross - amount,
            amount,
        };
    });
    const taxes = (order.prices.taxDetails.subtotals || []).flatMap((subtotal) =>
        (subtotal.tax_groups || []).map((group) => ({
            name: group.group_name,
            amount: group.tax_amount_currency,
        })));
    return {
        rows, discounts, promotions, taxes,
        gross: order.priceExcl + discounts + promotions,
        subtotal: order.priceExcl,
        tax: order.amountTaxes,
        rounding: order.totalDue - order.priceIncl,
        total: order.totalDue,
    };
}
