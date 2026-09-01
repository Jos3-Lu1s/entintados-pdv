
import { _t } from "@web/core/l10n/translation";
import { formatPoints } from "@entintados_pdv/app/utils/tint_points";

/**
 * Utilidades para transformar fórmulas y bases en líneas de orden enlazadas (padre e hijas de colorante).
 */

// Colorantes ya advertidos por falta de precio en la sesión actual.
const _warnedColorantsWithoutPrice = new Set();

/** Obtiene el precio de venta por punto del colorante. */
export function colorantPointPrice(colorant) {
    if (!colorant) {
        return 0;
    }
    const tmpl = colorant.product_tmpl_id;
    const price = colorant.lst_price ?? colorant.list_price ?? tmpl?.list_price ?? 0;
    if (!price && !_warnedColorantsWithoutPrice.has(colorant.id)) {
        _warnedColorantsWithoutPrice.add(colorant.id);
        console.warn(
            "[ENTINTADOS] El colorante «%s» no tiene precio de venta: se cobrará en cero. " +
                "Revisa el campo «Precio de venta» en el producto.",
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

/** Obtiene la regla de rango mínimo/máximo configurada para la base y presentación. */
export function resolvePresentationRange(pos, baseProduct) {
    if (!baseProduct || !pos?.models?.["lines.product.presentation"]) {
        return { priceMin: 0, priceMax: 0, priceOsel: 0, hasRange: false };
    }
    const tmpl = baseProduct.product_tmpl_id || baseProduct;
    const lineId = tmpl.lines_product_id?.id ?? tmpl.lines_product_id;
    const sizeId = tmpl.tint_size_id?.id ?? tmpl.tint_size_id;

    if (!lineId || !sizeId) {
        return { priceMin: 0, priceMax: 0, priceOsel: 0, hasRange: false };
    }

    const presentations = pos.models["lines.product.presentation"].getAll?.() || [];
    const match = presentations.find((p) => {
        const pLineId = p.line_id?.id ?? p.line_id;
        const pSizeId = p.presentation_id?.id ?? p.presentation_id;
        return pLineId === lineId && pSizeId === sizeId;
    });

    if (!match) {
        return { priceMin: 0, priceMax: 0, priceOsel: 0, hasRange: false };
    }

    const priceMin = match.price_min || 0;
    const priceMax = match.price_max || 0;
    const priceOsel = match.price_osel || 0;
    return {
        priceMin,
        priceMax,
        priceOsel,
        hasRange: priceMin > 0 || priceMax > 0,
    };
}

/**
 * Calcula los detalles completos del precio entintado (teórico, acotado y estado de rango).
 */
export function computeTintedPriceDetails(pos, baseProduct, formula) {
    const basePrice =
        baseProduct?.lst_price ?? baseProduct?.product_tmpl_id?.list_price ?? 0;
    const colorantsPrice = formulaColorantPrice(pos, formula);
    const theoreticalPrice = basePrice + colorantsPrice;
    const range = resolvePresentationRange(pos, baseProduct);

    let finalPrice = theoreticalPrice;
    let status = "normal";

    if (range.priceMin > 0 && theoreticalPrice < range.priceMin) {
        finalPrice = range.priceMin;
        status = "adjusted_min";
    } else if (range.priceMax > 0 && theoreticalPrice > range.priceMax) {
        finalPrice = range.priceMax;
        status = "adjusted_max";
    }

    return {
        basePrice,
        colorantsPrice,
        theoreticalPrice,
        finalPrice,
        status,
        range,
    };
}

/** Calcula el precio total del producto entintado aplicando las reglas de acotamiento de rango. */
export function computeTintedPrice(pos, baseProduct, formula) {
    return computeTintedPriceDetails(pos, baseProduct, formula).finalPrice;
}

/** Calcula los litros a extraer según el porcentaje de extracción de la base. */
export function extractionLiters(baseType, size) {
    if (!baseType?.requires_extraction || !size) {
        return 0;
    }
    return (size.volume_liters * (baseType.extraction_percentage || 0)) / 100;
}

/** Genera la nota comercial limpia y confidencial para el cliente (sin revelar receta). */
export function buildCustomerNote({ color, gallery }) {
    const galleryName = gallery?.name || "";
    const colorCode = color?.code ? `[${color.code}] ` : "";
    const colorName = color?.name || "";
    if (!colorName && !colorCode) {
        return "";
    }
    let text = _t("Color: %(code)s%(name)s", {
        code: colorCode,
        name: colorName,
    });
    if (galleryName) {
        text += ` (${galleryName})`;
    }
    return text;
}

/** Genera la receta técnica detallada para uso exclusivo interno (taller / etiqueta de bote). */
export function buildInternalTechnicalText(pos, { color, baseType, size, formula }) {
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

/** Genera el texto resumen para la nota pública de la línea (mantiene confidencialidad). */
export function buildSummaryText(pos, { color, baseType, size, formula }) {
    const gallery = formula?.gallery_id;
    return buildCustomerNote({ color, gallery });
}

/**
 * Agrega a la orden activa la línea base con el precio total consolidado y sus líneas hijas de colorantes a $0.00.
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
    const priceDetails = computeTintedPriceDetails(pos, baseProduct, formula);
    const finalUnitPrice = priceDetails.finalPrice;

    // Las líneas hijas representan el consumo físico de colorante para inventario a $0.00 comercial.
    const comboLines = formulaDoses(pos, formula).map((dose) => [
        "create",
        {
            product_id: dose.colorant,
            order_id: order,
            qty: dose.points * qty,
            price_unit: 0.0,
            price_type: "manual",
            manual_price: true,
            pricelist: pricelist,
            tax_ids: productTaxes(dose.colorant).map((tax) => ["link", tax]),
            customer_note: "",
            unit_points: dose.points,
            is_tint_colorant: true,
        },
    ]);

    // Cotiza y agrega la base con el precio consolidado final.
    const parent = await pos.addLineToCurrentOrder(
        {
            product_tmpl_id: baseTmpl,
            product_id: baseProduct,
            qty,
            price_unit: finalUnitPrice,
            combo_line_ids: comboLines,
            is_tinted_base: true,
        },
        {
            price_unit: finalUnitPrice,
            pricelist: pricelist,
        },
        false
    );

    if (parent) {
        parent.is_tinted_base = true;
        if (parent.combo_line_ids) {
            const doses = formulaDoses(pos, formula);
            parent.combo_line_ids.forEach((child, index) => {
                child.is_tint_colorant = true;
                if (child.unit_points === undefined || child.unit_points === null) {
                    child.unit_points = doses[index]?.points ?? (qty ? child.qty / qty : 0);
                }
            });
        }
        if (typeof parent.setUnitPrice === "function") {
            parent.setUnitPrice(finalUnitPrice);
        } else if (typeof parent.set_unit_price === "function") {
            parent.set_unit_price(finalUnitPrice);
        } else {
            parent.price_unit = finalUnitPrice;
        }
        parent.price_type = "manual";
        parent.manual_price = true;

        const partner = order.get_partner?.() || order.partner_id;
        const partnerDiscount = partner?.discount || 0;
        if (partnerDiscount > 0) {
            if (typeof parent.setDiscount === "function") {
                parent.setDiscount(partnerDiscount);
            } else if (typeof parent.set_discount === "function") {
                parent.set_discount(partnerDiscount);
            } else {
                parent.discount = partnerDiscount;
            }
        }

        const gallery = formula?.gallery_id;
        parent.setCustomerNote(
            buildCustomerNote({ color, gallery })
        );
    }
    return parent;
}
