/** @odoo-module **/

/**
 * Notación mixta de puntos de colorante, espejo de `utils/points.py`.
 *
 * Vive aparte del popup a propósito: el helper de orden y las pantallas
 * necesitan formatear puntos sin arrastrar la dependencia de un componente
 * OWL completo.
 */

/** Puntos que contiene una onza de colorante. Estándar físico del oficio. */
export const POINTS_PER_OUNCE = 48;

/** Símbolo con el que la operación denota las onzas. */
export const OUNCE_SYMBOL = "Y";

/** Descompone un total de puntos en `[onzas, puntos restantes]`. */
export function splitPoints(points) {
    const total = Math.round(points || 0);
    const sign = total < 0 ? -1 : 1;
    const abs = Math.abs(total);
    return [sign * Math.trunc(abs / POINTS_PER_OUNCE), sign * (abs % POINTS_PER_OUNCE)];
}

/**
 * Formatea puntos en la notación de las tablas del fabricante.
 *
 * 456 -> "9Y 24" · 96 -> "2Y" · 24 -> "24 Pts."
 */
export function formatPoints(points) {
    const total = Math.round(points || 0);
    if (total < 0) {
        return `-${formatPoints(-total)}`;
    }
    const [ounces, rest] = splitPoints(total);
    if (ounces && rest) {
        return `${ounces}${OUNCE_SYMBOL} ${rest}`;
    }
    if (ounces) {
        return `${ounces}${OUNCE_SYMBOL}`;
    }
    return `${rest} Pts.`;
}
