/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { formatPoints } from "@entintados_pdv/app/utils/tint_points";

/**
 * Punto único donde una venta entintada se convierte en líneas de orden.
 *
 * Antes esta lógica estaba copiada en tres sitios (el botón de control, el
 * widget de la pantalla de productos y la pantalla de colores), lo que hacía
 * que cualquier corrección hubiera que aplicarla tres veces.
 *
 * ## Estructura de líneas
 *
 * La base es la línea padre y cada colorante de la fórmula es una línea hija
 * enlazada por `combo_parent_id`. No se usa `combo_item_id` porque no hay un
 * `product.combo` real detrás: sólo se aprovecha el árbol padre/hijo, que el
 * núcleo trata de forma independiente (`isPartOfCombo`, `getAllLinesInCombo`
 * y la propagación de cantidad en `setQuantity` sólo miran los campos de
 * parentesco).
 *
 * Son líneas reales, no adornos: cada una genera su movimiento de stock, así
 * que los puntos de colorante se descuentan del inventario, se valoran al
 * costo y llegan a la factura.
 */

/**
 * Precio de venta de un punto de este colorante.
 *
 * Un colorante sin precio por punto se vendería a cero sin que nadie lo note
 * hasta cuadrar caja, así que se avisa en consola. La causa habitual es que
 * el campo «Precio por punto» quedó vacío en la ficha del producto.
 */
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

/** Impuestos del producto, tolerante a dónde viva el campo. */
function productTaxes(product) {
    return product?.product_tmpl_id?.taxes_id ?? product?.taxes_id ?? [];
}

/** Dosis de la fórmula ordenadas por secuencia, con el colorante resuelto. */
export function formulaDoses(pos, formula) {
    return [...(formula?.line_ids || [])]
        .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
        .map((line) => {
            const colorantId = line.colorant_id?.id ?? line.colorant_id;
            const colorant = pos.models["product.product"].get(colorantId);
            return {
                colorant,
                points: line.points || 0,
                name:
                    colorant?.display_name ||
                    line.colorant_id?.display_name ||
                    _t("(colorante)"),
            };
        })
        .filter((dose) => dose.colorant && dose.points > 0);
}

/** Costo de colorante de una fórmula, a precio de venta por punto. */
export function formulaColorantPrice(pos, formula) {
    return formulaDoses(pos, formula).reduce(
        (acc, dose) => acc + dose.points * colorantPointPrice(dose.colorant),
        0
    );
}

/**
 * Precio total del envase entintado: base más colorante.
 *
 * Es lo que paga el cliente independientemente de cómo se reparta entre
 * líneas, así que sirve tanto para mostrarlo en el asistente como para
 * comparar combinaciones de un mismo color.
 */
export function computeTintedPrice(pos, baseProduct, formula) {
    const base = baseProduct?.lst_price ?? baseProduct?.product_tmpl_id?.list_price ?? 0;
    return base + formulaColorantPrice(pos, formula);
}

/** Litros a extraer antes de entintar, o 0 si esta base no lo requiere. */
export function extractionLiters(baseType, size) {
    if (!baseType?.requires_extraction || !size) {
        return 0;
    }
    return (size.volume_liters * (baseType.extraction_percentage || 0)) / 100;
}

/**
 * Resumen legible que se guarda como nota de cliente en la línea de la base.
 *
 * Es lo que ve el operador que dispensa y lo que se imprime en la etiqueta,
 * así que incluye la dosis en la notación mixta del oficio y el aviso de
 * extracción previa cuando aplica.
 */
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
 * Agrega a la orden actual un envase entintado completo.
 *
 * Las líneas de colorante se crean en la misma llamada que la base, vía
 * `combo_line_ids`, que es como lo hace el núcleo con los combos. Hacerlo en
 * una sola creación evita que las hijas se fusionen con otras líneas de la
 * orden antes de quedar enlazadas al padre.
 *
 * @param {Object} pos          servicio `pos`
 * @param {Object} args.baseProduct  `product.product` de la base
 * @param {Object} args.formula      `tint.color.formula` a aplicar
 * @param {Object} args.color        `tint.color` elegido
 * @param {Number} [args.qty=1]      envases a vender
 * @returns {Object|undefined} la línea padre creada
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
            // Fijo a propósito: el precio del punto es del catálogo de
            // entintado, no del tarifario general, así que no debe
            // recalcularse al cambiar la cantidad.
            price_type: "manual",
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

    // La base se cotiza por el tarifario normal: no se pasa `price_unit`
    // para que respete listas de precios y descuentos.
    const parent = await pos.addLineToCurrentOrder(
        {
            product_tmpl_id: baseTmpl,
            product_id: baseProduct,
            qty,
            combo_line_ids: comboLines,
        },
        {},
        false
    );

    if (parent) {
        parent.setCustomerNote(
            buildSummaryText(pos, { color, baseType, size, formula })
        );
    }
    return parent;
}
