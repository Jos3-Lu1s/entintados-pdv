/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { TintFormulaPopup } from "@entintados_pdv/js/tint_formula_popup";
import {
    addTintedBaseToOrder,
    extractionLiters,
} from "@entintados_pdv/app/utils/tint_order";

/**
 * El recorrido de entintar, de principio a fin, en un solo lugar.
 *
 * `tint_order.js` sabe convertir una fórmula en líneas de orden. Este módulo
 * sabe la secuencia que lleva hasta ahí: validar la base, preguntar el color,
 * resolver la fórmula y avisar al cajero.
 *
 * Están separados a propósito: uno es lógica de negocio pura y comprobable,
 * el otro necesita diálogos y notificaciones. Antes esta secuencia estaba
 * repetida en los tres puntos de entrada —botón de control, clic en la
 * grilla y pantalla de colores—, que es donde se colaban las diferencias de
 * comportamiento entre una vía y otra.
 */

/**
 * @param {Object} ctx            componente con `pos`, `dialog` y `notification`
 * @param {Object} baseProduct    `product.product` de la base a entintar
 * @param {Object} [replaceLine]  línea suelta a sustituir por el grupo entintado
 * @param {Number} [qty=1]        envases a vender
 * @param {Number|false} [initialColorId]  color preseleccionado en el popup
 * @returns {Object|undefined} la línea padre creada, o undefined si se canceló
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

    // Una línea suelta no puede convertirse en padre: hay que recrearla.
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

/**
 * Entintado desde una tarjeta del panel.
 *
 * Aquí no hace falta el popup: la tarjeta ya es una fórmula concreta, así que
 * base, presentación y color están decididos. Lo único que sigue requiriendo
 * intervención es el acuse de extracción previa, y solo en las bases que lo
 * exigen.
 */
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
