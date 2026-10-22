import { Component } from "@odoo/owl";
import { formatFloat, formatMonetary } from "@web/views/fields/formatters";

/**
 * Productos del paquete con el mismo criterio que `getComboRecords` de `sale`, que no se exporta:
 * las filas con `combo_item_id` que siguen a la del paquete y apuntan a ella.
 */
function getComboItemRecords(packageRecord) {
    const records = packageRecord._parentRecord?.data.order_line?.records || [];
    const items = [];
    for (const record of records.slice(records.indexOf(packageRecord) + 1)) {
        if (
            !record.data.combo_item_id?.id ||
            (record.data.linked_line_id?.id !== packageRecord.resId &&
                record.data.linked_virtual_id !== packageRecord.data.virtual_id)
        ) {
            break;
        }
        items.push(record);
    }
    return items;
}

function formatQuantity(value) {
    return Number.isInteger(value) ? String(value) : formatFloat(value, { digits: [16, 2] });
}

/** Desglose del reparto nativo del precio del paquete, armado con la foto de cada producto. */
export function buildComboBreakdown(packageRecord) {
    const items = getComboItemRecords(packageRecord);
    const snapshot = items.find((item) => item.data.combo_price_snapshot)?.data
        .combo_price_snapshot;
    if (!snapshot) {
        return { empty: true };
    }
    const currencyId = snapshot.currency_id;
    const money = (value) => formatMonetary(value, { currencyId });
    const number = (value) => formatFloat(value, { digits: [16, 2] });

    const groups = snapshot.groups.map((group) => {
        const item = items.find(
            (record) => record.data.combo_price_snapshot?.combo_id === group.combo_id
        );
        const own = item?.data.combo_price_snapshot;
        if (!own) {
            return { name: group.name, referenceProduct: group.reference_product, missing: true };
        }
        const adjustment = own.rounding_adjustment;
        return {
            name: group.name,
            referenceProduct: group.reference_product,
            formula: `${number(group.reference)} / ${number(snapshot.total_reference)} × ${number(
                snapshot.package_price
            )} = ${number(own.share_rounded)}`,
            adjustment: adjustment
                ? `${number(own.share_rounded)} ${adjustment < 0 ? "−" : "+"} ${number(
                      Math.abs(adjustment)
                  )} = ${number(own.share_rounded + adjustment)}`
                : false,
            chosenProduct: item.data.product_id?.display_name || "",
            extraPrice: own.extra_price ? money(own.extra_price) : false,
            attributesExtra: own.attributes_extra ? money(own.attributes_extra) : false,
            unitPrice: money(own.unit_price),
            manualPrice: item.data.price_origin === "manual" ? money(item.data.price_unit) : false,
        };
    });
    const packageTotal = items.reduce((total, item) => total + item.data.price_unit, 0);
    const quantityTotal = items.reduce(
        (total, item) => total + item.data.price_unit * item.data.product_uom_qty,
        0
    );
    return {
        empty: false,
        packagePrice: money(snapshot.package_price),
        packagePriceOrigin: snapshot.package_price_origin,
        referenceSum: `${snapshot.groups
            .map((group) => number(group.reference))
            .join(" + ")} = ${number(snapshot.total_reference)}`,
        groups,
        packageTotal: money(packageTotal),
        quantity: formatQuantity(packageRecord.data.product_uom_qty),
        quantityTotal: money(quantityTotal),
        hasDiscount: items.some((item) => item.data.discount),
    };
}

export class ComboBreakdownPopover extends Component {
    static template = "entintados_pdv.ComboBreakdownPopover";
    static props = {
        breakdown: Object,
        close: { type: Function, optional: true },
    };
}
