/**
 * Reglas de los combos en la caja, con los mismos criterios que Ventas: el paquete lleva el
 * acuerdo o la promoción, sus productos lo copian, y el precio del paquete (tarifa o precio
 * fijo) se reparte entre ellos.
 *
 * Funciones puras: leen las líneas como los modelos del PDV y no escriben nada.
 */

import { COMBO_ORIGIN_PREFIX } from "@entintados_pdv/app/utils/origin_badge";

/** Línea del paquete de un combo. La base entintada también tiene hijos, pero no es combo. */
export function isComboPackage(line) {
    return line?.product_id?.type === "combo";
}

/** Producto de un combo. Los colorantes de un entintado tienen padre, pero no `combo_item_id`. */
export function isComboItem(line) {
    return Boolean(line?.combo_item_id);
}

function packageQuantity(packageLine) {
    return Math.abs(Number(packageLine?.qty)) || 1;
}

function quantityPerPackage(line, packageQty) {
    return (Number(line.qty) || 0) / (packageQty || 1);
}

/**
 * Productos incluidos y extras de un paquete, con el criterio de `getFreeAndExtraChildLines`
 * del core (`qty_free` por combo, en el orden de las líneas), pero por paquete: el core compara
 * contra la cantidad total y con varios paquetes trataría todo como extra.
 */
export function splitComboLines(packageLine) {
    const packageQty = packageQuantity(packageLine);
    const remainingFree = new Map();
    const freeLines = [];
    const extraLines = [];
    for (const line of packageLine?.combo_line_ids || []) {
        if (!isComboItem(line)) {
            continue;
        }
        const qty = quantityPerPackage(line, packageQty);
        if (!qty) {
            continue;
        }
        const combo = line.combo_item_id.combo_id;
        const key = combo?.id ?? combo;
        if (!remainingFree.has(key)) {
            remainingFree.set(key, Number(combo?.qty_free ?? 0));
        }
        const left = remainingFree.get(key) - qty;
        if (left >= 0) {
            remainingFree.set(key, left);
            freeLines.push(line);
        } else {
            extraLines.push(line);
        }
    }
    return { freeLines, extraLines, packageQty };
}

function basePrice(line) {
    return Number(line.combo_item_id?.combo_id?.base_price || 0);
}

/** `extra_price` del producto en el combo más el `price_extra` de sus atributos. */
function extraCharges(line) {
    const attributes = (line.attribute_value_ids || []).reduce(
        (total, value) => total + Number(value?.price_extra || 0),
        0
    );
    return Number(line.combo_item_id?.extra_price || 0) + attributes;
}

/**
 * Precio unitario de cada producto del combo, como `computeComboItems` del core pero con el
 * `total` como parámetro (precio de tarifa o precio fijo del paquete) y calculado por paquete.
 *
 * @param {Object} params
 * @param {number} params.total - Precio de un paquete que se reparte entre los incluidos.
 * @param {number} params.packageQty - Cantidad de paquetes de la línea.
 * @param {Array} params.freeLines - Productos incluidos, de `splitComboLines`.
 * @param {Array} params.extraLines - Productos extra, de `splitComboLines`.
 * @param {{ round: (value: number) => number }} [params.rounding] - Moneda de la caja.
 * @returns {Map<Object, number>}
 */
export function distributeComboPrice({ total, packageQty = 1, freeLines = [], extraLines = [], rounding }) {
    const round = (value) => (rounding ? rounding.round(value) : value);
    const amount = Number(total) || 0;
    const prices = new Map();
    const referenceTotal = freeLines.reduce(
        (sum, line) => sum + basePrice(line) * quantityPerPackage(line, packageQty),
        0
    );
    let remaining = amount;
    freeLines.forEach((line, index) => {
        let price = referenceTotal ? round((basePrice(line) * amount) / referenceTotal) : 0;
        remaining -= price * quantityPerPackage(line, packageQty);
        // El último incluido absorbe el residuo del redondeo.
        if (index === freeLines.length - 1) {
            price = round(price + remaining);
        }
        prices.set(line, price + extraCharges(line));
    });
    for (const line of extraLines) {
        prices.set(line, round(basePrice(line)) + extraCharges(line));
    }
    return prices;
}

/** Descuento y badge que un producto del combo copia de su paquete. */
export function comboItemAgreementValues(packageLine) {
    const origin = packageLine.pricing_rule_origin || "";
    return {
        discount: Number(packageLine.discount || 0),
        pricing_rule_type: packageLine.pricing_rule_type || "none",
        pricing_rule_origin: origin ? `${COMBO_ORIGIN_PREFIX}${origin}` : "",
        pricing_rule_id: packageLine.pricing_rule_id || false,
    };
}

/** Leyenda del origen del precio de un producto del combo, la misma que en Ventas. */
export function comboItemPriceLabel(packageLine, itemLine) {
    const packageName = packageLine.product_id?.name || packageLine.product_id?.display_name || "";
    const comboName = itemLine.combo_item_id?.combo_id?.name || "";
    return `Parte del combo «${packageName}»: ${comboName}`;
}
