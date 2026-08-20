
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { TintFormulaPopup } from "@entintados_pdv/js/tint_formula_popup";
import {
    addTintedBaseToOrder,
    extractionLiters,
} from "@entintados_pdv/app/utils/tint_order";

/**
 * Flujo interactivo de entintado: valida la base, solicita color/fórmula y agrega el producto entintado.
 */

/**
 * Ejecuta el flujo de entintado abriendo el popup de selección de fórmula.
 * @param {Object} ctx - Contexto con servicios pos, dialog y notification.
 * @param {Object} params - Parámetros de la base, línea a reemplazar, cantidad y color inicial.
 * @returns {Promise<Object|undefined>} Línea padre creada o undefined si se cancela.
 */
export async function runTintFlow(
    ctx,
    { baseProduct, replaceLine = null, qty = 1, initialColorId = false }
) {
    const tmpl = baseProduct?.product_tmpl_id;

    if (!tmpl || tmpl.tint_role !== "base") {
        ctx.notification.add(
            _t("Ese producto no es una base de pintura entintable."),
            { type: "warning" }
        );
        return undefined;
    }

    if (!tmpl.tint_base_type_id || !tmpl.tint_size_id) {
        ctx.notification.add(
            _t("Esta base no tiene tipo o presentación configurados."),
            { type: "warning" }
        );
        return undefined;
    }

    const payload = await makeAwaitable(ctx.dialog, TintFormulaPopup, {
        baseTypeId: tmpl.tint_base_type_id.id,
        sizeId: tmpl.tint_size_id.id,
        initialColorId,
    });
    if (!payload) {
        return undefined;
    }

    const formula = ctx.pos.models["tint.color.formula"].get(payload.formulaId);
    const color = ctx.pos.models["tint.color"].get(payload.colorId);

    // Elimina la línea original para sustituirla por la estructura entintada.
    replaceLine?.delete();

    const parent = await addTintedBaseToOrder(ctx.pos, {
        baseProduct,
        formula,
        color,
        qty,
    });

    ctx.notification.add(
        parent
            ? _t("Entintado y materiales agregados a la orden.")
            : _t("No se pudo agregar el entintado a la orden."),
        { type: parent ? "success" : "danger" }
    );
    return parent;
}

/** Agrega un entintado directamente desde una tarjeta del panel, solicitando confirmación de extracción si aplica. */
export async function addTintedFromCard(ctx, { baseProduct, formula, color, qty = 1 }) {
    const tmpl = baseProduct?.product_tmpl_id;
    const baseType = tmpl?.tint_base_type_id;
    const liters = extractionLiters(baseType, tmpl?.tint_size_id);

    if (liters) {
        const acknowledged = await new Promise((resolve) => {
            ctx.dialog.add(ConfirmationDialog, {
                title: _t("Extracción previa"),
                body: _t(
                    "Antes de entintar hay que extraer %(liters)s L del envase. %(note)s",
                    {
                        liters: liters.toFixed(1),
                        note: baseType?.operator_note || "",
                    }
                ),
                confirmLabel: _t("Ya se extrajo"),
                cancelLabel: _t("Cancelar"),
                confirm: () => resolve(true),
                cancel: () => resolve(false),
            });
        });
        if (!acknowledged) {
            return undefined;
        }
    }

    const parent = await addTintedBaseToOrder(ctx.pos, {
        baseProduct,
        formula,
        color,
        qty,
    });

    ctx.notification.add(
        parent
            ? _t("Entintado agregado a la orden.")
            : _t("No se pudo agregar el entintado a la orden."),
        { type: parent ? "success" : "danger" }
    );
    return parent;
}
