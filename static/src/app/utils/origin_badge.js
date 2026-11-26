/**
 * Texto del badge de origen de la línea, igual que `utils/origin_badge.py` de Ventas:
 * `Nombre · 30%`. El color del badge ya indica el tipo, así que el texto solo lleva
 * lo que lo distingue.
 */

export const BADGE_SEPARATOR = " · ";

/** Marca del producto de combo que copia el badge de su paquete. */
export const COMBO_ORIGIN_PREFIX = "↳ ";

/** Decimales de la precisión `Discount`. */
const DISCOUNT_DIGITS = 2;

/** Porcentaje sin ceros sobrantes: 30 → "30%", 12.5 → "12.5%". */
export function formatDiscountPercent(discount) {
    const number = Number(discount || 0).toFixed(DISCOUNT_DIGITS).replace(/\.?0+$/, "");
    return `${number}%`;
}

/**
 * `name · %`; `discount` puede ser un número o un término fijo como "Regalo".
 * Sin `discount` devuelve solo el nombre; sin nombre, solo el segundo término.
 */
export function formatOriginBadge(name, discount = null) {
    const cleanName = (name || "").trim();
    if (discount === null || discount === undefined || discount === false) {
        return cleanName;
    }
    const term = typeof discount === "string" ? discount : formatDiscountPercent(discount);
    return [cleanName, term].filter(Boolean).join(BADGE_SEPARATOR);
}
