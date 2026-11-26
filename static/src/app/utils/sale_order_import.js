/**
 * Reglas puras para las líneas de una cotización o pedido de Ventas traído a la caja.
 * Sin parches ni componentes: los usa `overrides/sale_order_import.js`.
 */

/** Línea del ticket traída de una línea de producto de Ventas (no anticipo ni deducción). */
export function isImportedSaleLine(line) {
    const saleLine = line?.sale_order_line_id;
    return Boolean(saleLine && !saleLine.is_downpayment);
}

/** Valores que dejan la línea como recién capturada, antes de `refreshOrderPricing`. */
export function currentRulesResetValues() {
    return {
        price_type: "original",
        discount: 0,
        pricing_rule_type: "none",
        pricing_rule_origin: "",
        pricing_rule_id: false,
        promo_reward_id: false,
    };
}

/**
 * Dosis `{ colorant, colorantId, points, name }` de la fórmula congelada en `tint_snapshot`,
 * con los productos cargados en la caja. `colorant` queda `null` si el producto no está cargado.
 */
export function snapshotDoses(models, tintSnapshot) {
    const products = models?.["product.product"];
    return (tintSnapshot?.doses || [])
        .filter((dose) => Number(dose.points) > 0)
        .map((dose) => ({
            colorant: products?.get(dose.product_id) || null,
            colorantId: dose.product_id,
            points: Number(dose.points),
            name: dose.name || "",
        }));
}

/**
 * Total cotizado de las líneas traídas: el `price_total` de Ventas prorrateado a la cantidad
 * del ticket. `quotedSaleLine` lleva la cantidad y el total de la línea de venta en la unidad
 * del producto, como los entrega `read_converted`.
 */
export function quotedTotal(lines) {
    let total = 0;
    for (const line of lines) {
        const quoted = line.quotedSaleLine;
        const quantity = Number(quoted?.product_uom_qty) || 0;
        if (!quantity) {
            continue;
        }
        total += (Number(quoted.price_total) || 0) / quantity * (Number(line.qty) || 0);
    }
    return total;
}
