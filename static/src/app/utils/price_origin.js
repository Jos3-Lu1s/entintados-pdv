/**
 * Origen del precio de una línea de la caja: icono, color, título y detalle, con las etiquetas
 * de la selección `price_origin` de Ventas.
 */

import { _t } from "@web/core/l10n/translation";
import { PRICE_ORIGIN_ICONS } from "@entintados_pdv/common/price_origin_display";
import { isComboPackage } from "@entintados_pdv/app/utils/combo_rules";
import { findPricelistRule, pricelistOriginLabel } from "@entintados_pdv/app/utils/pricelist_rules";

const ORIGIN_TITLES = {
    list: _t("Precio de venta"),
    pricelist: _t("Tarifa"),
    fixed_price: _t("Precio fijo de acuerdo"),
    manual: _t("Manual"),
    combo: _t("Parte de combo"),
    tint: _t("Entintado"),
};

function pricelistDetail(line) {
    const pricelist = line.order_id?.pricelist_id;
    if (!pricelist) {
        return _t("Precio determinado por tarifa activa");
    }
    const quantity = line.getQuantity ? line.getQuantity() : line.qty;
    return pricelistOriginLabel(pricelist, findPricelistRule(pricelist, line.product_id, quantity));
}

function defaultDetail(key, line) {
    switch (key) {
        case "pricelist":
            return pricelistDetail(line);
        case "fixed_price":
            return line.pricing_rule_origin || _t("Precio fijo");
        case "manual":
            return _t("Modificado de forma manual");
        case "combo":
            return ORIGIN_TITLES.combo;
        case "tint":
            return _t("Precio del entintado: base más colorantes");
        default:
            return _t("Precio de venta del producto");
    }
}

/**
 * `{ key, icon, color, title, detail }` de la línea, o `null` sin línea. El paquete de un combo
 * no tiene detalle: su icono abre el desglose del reparto.
 */
export function priceOriginData(line) {
    if (!line) {
        return null;
    }
    if (isComboPackage(line)) {
        return { key: "combo", ...PRICE_ORIGIN_ICONS.combo, title: _t("Desglose del combo"), detail: false };
    }
    const stored = line.price_origin || (line.fixed_price_locked ? "fixed_price" : "list");
    const key = PRICE_ORIGIN_ICONS[stored] ? stored : "list";
    return {
        key,
        ...PRICE_ORIGIN_ICONS[key],
        title: ORIGIN_TITLES[key],
        detail: line.price_origin_label || defaultDetail(key, line),
    };
}
