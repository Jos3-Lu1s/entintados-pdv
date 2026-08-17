
import { _t } from "@web/core/l10n/translation";
import { formatPoints } from "@entintados_pdv/app/utils/tint_points";

/**
 * Utilidades para transformar fórmulas y bases en líneas de orden enlazadas (padre e hijas de colorante).
 */

/** Obtiene el precio de venta por punto del colorante. */
export function colorantPointPrice(colorant) {
    if (!colorant) {
        return 0;
    }
    const tmpl = colorant.product_tmpl_id;
    const price = tmpl?.price_per_point ?? colorant.price_per_point ?? 0;
    if (!price) {
        console.warn(
            "[ENTINTADOS] El colorante «%s» no tiene precio por punto: se cobrará en cero. " +
                "Revisa el campo «Precio por punto» en la pestaña Entintado del producto.",
            colorant.display_name || colorant.name || colorant.id
        );
    }
    return price;
}

/** Obtiene los impuestos configurados en el producto o plantilla. */
function productTaxes(product) {
    return product?.product_tmpl_id?.taxes_id ?? product?.taxes_id ?? [];
}

/** Obtiene las dosis de la fórmula ordenadas por secuencia con sus productos colorante. */
export function formulaDoses(pos, formula) {
    return [...(formula?.line_ids || [])]
        .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
        .map((line) => {
            const colorantId = line.colorant_id?.id ?? line.colorant_id;
            const colorant = pos.models["product.product"].get(colorantId);
            return {
                id: line.id,
                colorant,
                colorantId,
                points: line.points || 0,
                name:
                    colorant?.display_name ||
                    line.colorant_id?.display_name ||
                    _t("(colorante)"),
            };
        })
        .filter((dose) => dose.colorant && dose.points > 0);
}

/** Calcula el precio total de los colorantes de una fórmula. */
export function formulaColorantPrice(pos, formula) {
    return formulaDoses(pos, formula).reduce(
        (acc, dose) => acc + dose.points * colorantPointPrice(dose.colorant),
        0
    );
}

/** Calcula el precio total del producto entintado (precio base + colorantes). */
export function computeTintedPrice(pos, baseProduct, formula) {
    const base = baseProduct?.lst_price ?? baseProduct?.product_tmpl_id?.list_price ?? 0;
    return base + formulaColorantPrice(pos, formula);
}

/** Calcula los litros a extraer según el porcentaje de extracción de la base. */
export function extractionLiters(baseType, size) {
    if (!baseType?.requires_extraction || !size) {
        return 0;
    }
    return (size.volume_liters * (baseType.extraction_percentage || 0)) / 100;
}

/** Genera el texto resumen para la nota de la línea (color, base, dosis y extracción). */
export function buildSummaryText(pos, { color, baseType, size, formula }) {
    const doses = formulaDoses(pos, formula);
    const totalPoints = doses.reduce((acc, dose) => acc + dose.points, 0);
    const parts = [
        `${color?.code ? `[${color.code}] ` : ""}${color?.name || ""}`,
        `${baseType?.code || ""} · ${size?.name || ""}`,
        ...doses.map((dose) => `${dose.name}: ${formatPoints(dose.points)}`),
        `Total: ${formatPoints(totalPoints)}`,
    ];
    const liters = extractionLiters(baseType, size);
    if (liters) {
        parts.push(_t("Extraer %s L antes de entintar", liters.toFixed(1)));
    }
    return parts.join(" | ");
}

/**
 * Agrega a la orden activa la línea base y sus líneas hijas de colorantes vinculadas.
 * @param {Object} pos - Servicio POS.
 * @param {Object} params - Base, fórmula, color y cantidad.
 * @returns {Promise<Object|undefined>} Línea padre creada.
 */
export async function addTintedBaseToOrder(
    pos,
    { baseProduct, formula, color, qty = 1 }
) {
    if (!baseProduct || !formula) {
        return undefined;
    }

    const order = pos.getOrder();
    if (!order) {
        return undefined;
    }

    const pricelist =
        order.pricelist ||
        pos.pricelist ||
        pos.default_pricelist ||
        pos.config?.pricelist_id ||
        (pos.models["product.pricelist"]?.getAll?.()?.[0]) ||
        false;

    const baseTmpl = baseProduct.product_tmpl_id;
    const baseType = baseTmpl?.tint_base_type_id;
    const size = baseTmpl?.tint_size_id;

    const comboLines = formulaDoses(pos, formula).map((dose) => [
        "create",
        {
            product_id: dose.colorant,
            order_id: order,
            qty: dose.points * qty,
            price_unit: colorantPointPrice(dose.colorant),
            // Precio manual fijo por punto según la configuración de entintado.
            price_type: "manual",
            manual_price: true,
            pricelist: pricelist,
            tax_ids: productTaxes(dose.colorant).map((tax) => ["link", tax]),
            customer_note: _t(
                "Entintado de %(base)s · %(points)s",
                {
                    base: baseProduct.display_name || "",
                    points: formatPoints(dose.points),
                }
            ),
        },
    ]);

    // Cotiza la base según tarifa y listas de precios activas.
    const parent = await pos.addLineToCurrentOrder(
        {
            product_tmpl_id: baseTmpl,
            product_id: baseProduct,
            qty,
            combo_line_ids: comboLines,
        },
        {
            pricelist: pricelist,
        },
        false
    );

    if (parent) {
        parent.setCustomerNote(
            buildSummaryText(pos, { color, baseType, size, formula })
        );
    }
    return parent;
}
