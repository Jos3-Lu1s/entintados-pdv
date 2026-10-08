export function requiresPosStock(product) {
    const template = product?.product_tmpl_id || product;
    return template?.type === "consu" && Boolean(product?.is_storable ?? template?.is_storable);
}

/** Total de salidas del ticket; las devoluciones no habilitan stock aún no recibido. */
export function stockError(order, product, quantity, excludedLine = null) {
    if (!requiresPosStock(product) || quantity <= 0 || order?.preset_id?.is_return) {
        return null;
    }
    const used = (order?.lines || []).reduce((total, line) =>
        total + (line !== excludedLine && line !== excludedLine?._stockMergeSource && line.product_id?.id === product.id
            ? Math.max(0, Number(line.qty) || 0) : 0), 0);
    const available = Number(product.pos_stock_qty) || 0;
    const rounding = product.uom_id?.rounding || 0.00001;
    if (Math.round((used + quantity) / rounding) > Math.round(available / rounding)) {
        return `Existencias insuficientes para ${product.display_name || product.name}. ` +
            `Disponibles: ${available}; solicitadas en el ticket: ${used + quantity}.`;
    }
    return null;
}
