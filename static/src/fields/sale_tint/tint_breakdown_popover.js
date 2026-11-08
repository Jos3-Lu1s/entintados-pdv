import { Component } from "@odoo/owl";
import { formatFloat, formatMonetary } from "@web/views/fields/formatters";

function tintSectionId(record) {
    const value = record.data.tint_section_id;
    return Array.isArray(value) ? value[0] : value?.id || value || false;
}

function isTintHeader(record) {
    return record.data.display_type === "line_section" && Boolean(record.data.tint_snapshot);
}

function formatTrimmed(value) {
    return Number.isInteger(value) ? String(value) : formatFloat(value, { digits: [16, 2] });
}

/**
 * Encabezado del entintado de una línea: ella misma si es el encabezado o, para la base y los
 * colorantes, la sección anterior a la que apuntan.
 */
function getTintHeader(records, record) {
    if (isTintHeader(record)) {
        return record;
    }
    const sectionId = tintSectionId(record);
    if (!sectionId) {
        return null;
    }
    for (let index = records.indexOf(record) - 1; index >= 0; index--) {
        const candidate = records[index];
        if (isTintHeader(candidate) && candidate.resId === sectionId) {
            return candidate;
        }
    }
    return null;
}

/** Base y colorantes: las filas que siguen al encabezado y apuntan a él. */
function getTintComponents(records, header) {
    const components = [];
    for (const record of records.slice(records.indexOf(header) + 1)) {
        if (record.data.display_type || tintSectionId(record) !== header.resId) {
            break;
        }
        components.push(record);
    }
    return components;
}

/** Fórmula del entintado con los importes de sus líneas; vacía si no hay componentes cargados. */
export function buildTintBreakdown(record) {
    const records = record._parentRecord?.data.order_line?.records || [];
    const header = getTintHeader(records, record);
    const components = header ? getTintComponents(records, header) : [];
    if (!components.length) {
        return { empty: true, title: header?.data.name || "" };
    }
    const snapshot = header.data.tint_snapshot;
    const base =
        components.find((line) => line.data.product_id?.id === snapshot.base_id) || components[0];
    const currencyId = header.data.currency_id?.id || base.data.currency_id?.id;
    const money = (value) => formatMonetary(value, { currencyId });
    return {
        empty: false,
        title: header.data.name,
        colorCode: snapshot.color_code || "",
        colorName: snapshot.color_name || "",
        base: {
            name: base.data.product_id?.display_name || "",
            qty: formatTrimmed(base.data.product_uom_qty),
            subtotal: money(base.data.price_subtotal),
        },
        colorants: components
            .filter((line) => line !== base)
            .map((line) => ({
                name: line.data.product_id?.display_name || "",
                qty: `${formatTrimmed(line.data.product_uom_qty)} ${
                    line.data.product_uom_id?.display_name || ""
                }`.trim(),
                subtotal: money(line.data.price_subtotal),
            })),
        total: money(components.reduce((total, line) => total + line.data.price_subtotal, 0)),
    };
}

export class TintBreakdownPopover extends Component {
    static template = "entintados_pdv.TintBreakdownPopover";
    static props = {
        breakdown: Object,
        close: { type: Function, optional: true },
    };
}
