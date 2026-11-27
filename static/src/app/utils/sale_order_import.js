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

const idOf = (value) => (value && typeof value === "object" ? value.id : value) || false;

/** Línea de venta de un producto de combo (tiene paquete y opción). */
export function isSaleComboItem(saleLine) {
    return Boolean(saleLine?.combo_item_id && saleLine?.linked_line_id);
}

function comboConfiguration(saleLine) {
    const attributeCustomValues = {};
    for (const value of saleLine.product_custom_attribute_value_ids || []) {
        const attribute = idOf(value?.custom_product_template_attribute_value_id);
        if (attribute) {
            attributeCustomValues[attribute] = value.custom_value || "";
        }
    }
    return {
        attribute_value_ids: (saleLine.product_no_variant_attribute_value_ids || []).map(idOf).filter(Boolean),
        attribute_custom_values: attributeCustomValues,
    };
}

/**
 * `payload` de `handleComboProduct` para un paquete de Ventas: `[childLineConf, comboExtraLines]`.
 * La cantidad va por paquete (cantidad de la línea ÷ cantidad del paquete). Incluidos y extras
 * se separan con `qty_free` de cada combo, en el orden de las líneas, como `splitComboLines`.
 */
export function comboPayloadFromSaleLines(packageSaleLine, saleLines) {
    const packageId = idOf(packageSaleLine);
    const packageQty = Math.abs(Number(packageSaleLine?.product_uom_qty)) || 1;
    const remainingFree = new Map();
    const included = [];
    const extras = [];
    for (const saleLine of saleLines || []) {
        if (!isSaleComboItem(saleLine) || idOf(saleLine.linked_line_id) !== packageId) {
            continue;
        }
        const qty = (Number(saleLine.product_uom_qty) || 0) / packageQty;
        if (!qty) {
            continue;
        }
        const combo = saleLine.combo_item_id.combo_id;
        const key = idOf(combo);
        if (!remainingFree.has(key)) {
            remainingFree.set(key, Number(combo?.qty_free ?? 0));
        }
        const left = remainingFree.get(key) - qty;
        const conf = { combo_item_id: saleLine.combo_item_id, qty, configuration: comboConfiguration(saleLine) };
        if (left >= 0) {
            remainingFree.set(key, left);
            included.push(conf);
        } else {
            extras.push(conf);
        }
    }
    return [included, extras];
}

/**
 * Empareja cada producto creado del combo con su línea de venta por `combo_item_id`, en orden.
 * Devuelve `Map<posLine, saleLine>`. Las líneas que no emparejan quedan fuera.
 */
export function matchComboItemSaleLines(comboLines, itemSaleLines) {
    const pending = [...(itemSaleLines || [])];
    const matches = new Map();
    for (const line of comboLines || []) {
        const itemId = idOf(line?.combo_item_id);
        const index = itemId ? pending.findIndex((saleLine) => idOf(saleLine.combo_item_id) === itemId) : -1;
        if (index >= 0) {
            matches.set(line, pending.splice(index, 1)[0]);
        }
    }
    return matches;
}

/** Valores `create` de las hijas de colorante a $0, a partir de `snapshotDoses`. */
export function tintColorantChildValues(doses, qty) {
    const containers = Number(qty) || 0;
    return (doses || [])
        .filter((dose) => dose.colorant && Number(dose.points) > 0)
        .map((dose) => ({
            product_id: dose.colorant,
            qty: dose.points * containers,
            price_unit: 0,
            price_type: "manual",
            unit_points: dose.points,
            is_tint_colorant: true,
        }));
}
