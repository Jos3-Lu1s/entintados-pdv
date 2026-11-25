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

/** `price_extra` de los atributos del producto. */
function attributesExtra(line) {
    return (line.attribute_value_ids || []).reduce(
        (total, value) => total + Number(value?.price_extra || 0),
        0
    );
}

/** `extra_price` del producto en el combo más el `price_extra` de sus atributos. */
function extraCharges(line) {
    return Number(line.combo_item_id?.extra_price || 0) + attributesExtra(line);
}

/**
 * Parte del precio del paquete de cada producto incluido: su referencia (`base_price` por la
 * cantidad por paquete), la parte redondeada y, en el último, el ajuste que absorbe el residuo.
 * Es el cálculo que comparten el reparto y su desglose.
 */
function comboShares({ total, packageQty = 1, freeLines = [], rounding }) {
    const round = (value) => (rounding ? rounding.round(value) : value);
    const amount = Number(total) || 0;
    const referenceTotal = freeLines.reduce(
        (sum, line) => sum + basePrice(line) * quantityPerPackage(line, packageQty),
        0
    );
    let remaining = amount;
    return {
        referenceTotal,
        shares: freeLines.map((line, index) => {
            const reference = basePrice(line) * quantityPerPackage(line, packageQty);
            const share = referenceTotal ? round((basePrice(line) * amount) / referenceTotal) : 0;
            remaining -= share * quantityPerPackage(line, packageQty);
            // El último incluido absorbe el residuo del redondeo.
            const adjustment = index === freeLines.length - 1 ? round(round(share + remaining) - share) : 0;
            return { line, reference, share, adjustment };
        }),
    };
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
    const prices = new Map();
    const { shares } = comboShares({ total, packageQty, freeLines, rounding });
    for (const { line, share, adjustment } of shares) {
        prices.set(line, (adjustment ? round(share + adjustment) : share) + extraCharges(line));
    }
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

/** La opción más barata del grupo, la que Ventas toma como referencia. */
function referenceProductName(combo) {
    const items = combo?.combo_item_ids || [];
    let cheapest = null;
    for (const comboItem of items) {
        if (!cheapest || Number(comboItem.product_id?.lst_price || 0) < Number(cheapest.product_id?.lst_price || 0)) {
            cheapest = comboItem;
        }
    }
    return cheapest?.product_id?.display_name || "";
}

/**
 * Desglose del precio del paquete, con la forma de `buildComboBreakdown` de Ventas para usar su
 * plantilla: precio que se reparte (tarifa o precio fijo), parte de cada producto incluido con su
 * ajuste por redondeo y descuento heredado del paquete.
 *
 * @param {Object} packageLine - Línea del paquete.
 * @param {Object} context
 * @param {number} context.pricelistPrice - Precio de tarifa de un paquete.
 * @param {{ originLabel: string, price: number }|null} context.agreement - Acuerdo de precio fijo
 *        vigente del paquete, o nada si el paquete no está en precio fijo.
 * @param {string} context.packagePriceOrigin - `Tarifa: <tarifa>` o `Precio de venta`.
 * @param {{ round: (value: number) => number }} [context.rounding] - Moneda de la caja.
 * @param {(value: number) => string} context.formatMoney
 * @param {(value: number) => string} context.formatNumber
 */
export function comboBreakdown(packageLine, context) {
    const { pricelistPrice = 0, agreement = null, packagePriceOrigin = "", rounding } = context;
    const money = context.formatMoney;
    const number = context.formatNumber;
    const { freeLines, extraLines, packageQty } = splitComboLines(packageLine);
    const items = [...freeLines, ...extraLines];
    if (!items.length) {
        return { empty: true };
    }
    const total = agreement ? Number(agreement.price) || 0 : Number(pricelistPrice) || 0;
    const { referenceTotal, shares } = comboShares({ total, packageQty, freeLines, rounding });
    const discounted = (line) => line.price_unit * (1 - (Number(line.discount) || 0) / 100);
    const perPackage = (line, amount) => amount * quantityPerPackage(line, packageQty);

    const groups = shares.map(({ line, reference, share, adjustment }) => {
        const combo = line.combo_item_id?.combo_id;
        const extraPrice = Number(line.combo_item_id?.extra_price || 0);
        const attributes = attributesExtra(line);
        return {
            name: combo?.name || "",
            referenceProduct: referenceProductName(combo),
            formula: `${number(reference)} / ${number(referenceTotal)} × ${number(total)} = ${number(share)}`,
            adjustment: adjustment
                ? `${number(share)} ${adjustment < 0 ? "−" : "+"} ${number(Math.abs(adjustment))} = ${number(
                      share + adjustment
                  )}`
                : false,
            chosenProduct: line.product_id?.display_name || line.product_id?.name || "",
            extraPrice: extraPrice ? money(extraPrice) : false,
            attributesExtra: attributes ? money(attributes) : false,
            unitPrice: money(line.price_unit),
            // El precio de un producto del combo no se teclea en la caja.
            manualPrice: false,
            discount: Number(line.discount)
                ? `${money(line.price_unit)} − ${number(Number(line.discount))} % = ${money(discounted(line))}`
                : false,
        };
    });
    const hasDiscount = items.some((line) => Number(line.discount));
    return {
        empty: false,
        pricelistPrice: money(pricelistPrice),
        packagePriceOrigin,
        agreement: agreement ? { label: agreement.originLabel, price: money(agreement.price) } : false,
        distributedPrice: money(total),
        referenceSum: `${shares.map(({ reference }) => number(reference)).join(" + ")} = ${number(referenceTotal)}`,
        groups,
        packageTotal: money(items.reduce((sum, line) => sum + perPackage(line, line.price_unit), 0)),
        discountOrigin: hasDiscount ? packageLine.pricing_rule_origin || "" : false,
        packageTotalDiscounted: hasDiscount
            ? money(items.reduce((sum, line) => sum + perPackage(line, discounted(line)), 0))
            : false,
        quantity: number(packageQty),
        quantityTotal: money(items.reduce((sum, line) => sum + (Number(line.priceExcl) || 0), 0)),
    };
}
