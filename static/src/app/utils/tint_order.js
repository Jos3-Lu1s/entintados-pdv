
import { _t } from "@web/core/l10n/translation";
import { recordId, resolveAgreement } from "@entintados_pdv/app/utils/agreement_rules";

/**
 * Utilidades para transformar fórmulas y bases en líneas de orden enlazadas (padre e hijas de colorante).
 */

// Colorantes ya advertidos por falta de precio en la sesión actual.
const _warnedColorantsWithoutPrice = new Set();

const NO_RANGE = Object.freeze({ priceMin: 0, priceMax: 0, priceOsel: 0, hasRange: false });

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
            const colorantId = recordId(line.colorant_id);
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

/** Obtiene la regla de rango mínimo/máximo configurada para la base y presentación. */
export function resolvePresentationRange(pos, baseProduct) {
    const presentations = pos?.models?.["lines.product.presentation"];
    const tmpl = baseProduct?.product_tmpl_id || baseProduct;
    const lineId = recordId(tmpl?.lines_product_id);
    const sizeId = recordId(tmpl?.tint_size_id);
    const match =
        presentations &&
        lineId &&
        sizeId &&
        (presentations.getAll?.() || []).find(
            (p) => recordId(p.line_id) === lineId && recordId(p.presentation_id) === sizeId
        );
    if (!match) {
        return NO_RANGE;
    }
    const priceMin = match.price_min || 0;
    const priceMax = match.price_max || 0;
    const priceOsel = match.price_osel || 0;
    return { priceMin, priceMax, priceOsel, hasRange: priceMin > 0 || priceMax > 0 };
}

/**
 * Calcula los detalles completos del precio entintado (teórico, acotado y estado de rango).
 * Si el cliente tiene un precio fijo acordado para la base, este sustituye el precio base
 * y se le suman los colorantes dispensados sin acotamiento forzado de rango.
 * `doses` permite reutilizar las dosis que el llamador ya calculó.
 */
export function computeTintedPriceDetails(
    pos,
    baseProduct,
    formula,
    partner = null,
    doses = formulaDoses(pos, formula)
) {
    let basePrice =
        baseProduct?.lst_price ?? baseProduct?.product_tmpl_id?.list_price ?? 0;

    let isFixedPrice = false;
    if (partner) {
        const rule = resolveAgreement(pos?.models, partner, baseProduct, { currency: pos?.currency });
        if (rule.type === "fixed_price") {
            basePrice = rule.price;
            isFixedPrice = true;
        }
    }

    const colorantsPrice = doses.reduce(
        (acc, dose) => acc + dose.points * colorantPointPrice(dose.colorant),
        0
    );
    const theoreticalPrice = basePrice + colorantsPrice;
    const range = resolvePresentationRange(pos, baseProduct);

    let finalPrice = theoreticalPrice;
    let status = "normal";

    if (!isFixedPrice) {
        if (range.priceMin > 0 && theoreticalPrice < range.priceMin) {
            finalPrice = range.priceMin;
            status = "adjusted_min";
        } else if (range.priceMax > 0 && theoreticalPrice > range.priceMax) {
            finalPrice = range.priceMax;
            status = "adjusted_max";
        }
    }

    return {
        basePrice,
        colorantsPrice,
        theoreticalPrice,
        finalPrice,
        status,
        range,
        isFixedPrice,
    };
}

/** Calcula los litros a extraer según el porcentaje de extracción de la base. */
export function extractionLiters(baseType, size) {
    if (!baseType?.requires_extraction || !size) {
        return 0;
    }
    return (size.volume_liters * (baseType.extraction_percentage || 0)) / 100;
}

/** Genera la nota comercial limpia y confidencial para el cliente (sin revelar receta). */
export function buildCustomerNote({ color }) {
    const colorCode = color?.code ? `[${color.code}] ` : "";
    const colorName = color?.name || "";
    if (!colorName && !colorCode) {
        return "";
    }
    return _t("Color: %(code)s%(name)s", {
        code: colorCode,
        name: colorName,
    });
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

    const doses = formulaDoses(pos, formula);
    const { finalPrice: finalUnitPrice } = computeTintedPriceDetails(
        pos,
        baseProduct,
        formula,
        order.getPartner(),
        doses
    );

    // Las líneas hijas representan el consumo físico de colorante para inventario a $0.00 comercial.
    const comboLines = doses.map((dose) => [
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
            product_tmpl_id: baseProduct.product_tmpl_id,
            product_id: baseProduct,
            qty,
            price_unit: finalUnitPrice,
            combo_line_ids: comboLines,
            is_tinted_base: true,
            tint_color_code: color?.code || false,
        },
        {
            price_unit: finalUnitPrice,
            pricelist: pricelist,
        },
        false
    );

    if (parent) {
        parent.is_tinted_base = true;
        parent.combo_line_ids?.forEach((child, index) => {
            child.is_tint_colorant = true;
            child.unit_points ??= doses[index]?.points ?? tintDose(child, parent);
        });
        parent.setUnitPrice(finalUnitPrice);
        parent.price_type = "manual";
        parent.manual_price = true;
        parent.setCustomerNote(buildCustomerNote({ color }));
        // Con la línea ya marcada como entintado, el acuerdo y el origen del precio los
        // aplica `_refreshTintedBaseAgreement`, igual que en cualquier otro cambio del pedido.
        order.refreshOrderPricing();
    }
    return parent;
}

export function isTintColorant(line) {
    return Boolean(line?.is_tint_colorant || line?.product_id?.tint_role === "colorant" ||
        line?.product_id?.product_tmpl_id?.tint_role === "colorant");
}

export function tintedProductName(line) {
    if (!isTintedBase(line)) {
        return "";
    }
    const product = line.product_id;
    const recipe = line.sale_order_line_id?.tint_snapshot || {};
    const code = recipe.color_code || line.tint_color_code || "";
    const color = code ? line.models?.["tint.color"]?.getAll()?.find((item) => item.code === code) : null;
    let name = recipe.color_name || color?.name || "";
    if (!name) {
        const note = (line.customer_note || "").split("\n").find((part) => /^\s*(Color:|\+)/.test(part)) || "";
        name = note.replace(/^\s*(?:Color:|\+)\s*/, "").trim();
        if (code && name.startsWith(`[${code}]`)) {
            name = name.slice(code.length + 2).trim();
        }
        const gallery = line.models?.["tint.gallery"]?.getAll()?.find(
            (item) => item.name && name.endsWith(` (${item.name})`));
        if (gallery) {
            name = name.slice(0, -(gallery.name.length + 3)).trim();
        }
    }
    const productCode = product?.default_code || product?.product_tmpl_id?.default_code || "";
    const baseName = product?.name || product?.display_name || "";
    const base = productCode && !baseName.startsWith(`[${productCode}] `)
        ? `[${productCode}] ${baseName}` : baseName;
    const colorName = [code ? `[${code}]` : "", name].filter(Boolean).join(" ");
    return [base, colorName].filter(Boolean).join(" + ");
}

export function isTintedBase(line) {
    return Boolean(line && !isTintColorant(line) && (line.is_tinted_base || line.tint_color_code ||
        line.combo_line_ids?.some(isTintColorant)));
}

export function tintParent(line) {
    const parent = line?.combo_parent_id || line;
    return isTintedBase(parent) ? parent : null;
}

export function tintDose(child, parent) {
    const source = child.refunded_orderline_id || child;
    const sourceParent = source.combo_parent_id || parent.refunded_orderline_id || parent;
    return Number(child.unit_points ?? (sourceParent.qty ? source.qty / sourceParent.qty : 0));
}
