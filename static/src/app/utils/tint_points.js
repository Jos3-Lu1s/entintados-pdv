
/**
 * Utilidades para el cálculo y formato de puntos de colorante en notación mixta (Y/Pts).
 */

/** Puntos por onza de colorante (48 puntos = 1Y). */
export const POINTS_PER_OUNCE = 48;

/** Símbolo que denota las onzas de colorante. */
export const OUNCE_SYMBOL = "Y";

/** Descompone puntos totales en onzas (Y) y puntos sobrantes. */
export function splitPoints(points) {
    const total = Math.round(points || 0);
    const sign = total < 0 ? -1 : 1;
    const abs = Math.abs(total);
    return [sign * Math.trunc(abs / POINTS_PER_OUNCE), sign * (abs % POINTS_PER_OUNCE)];
}

/** Formatea puntos a texto en notación tradicional (ej. "9Y 24", "2Y", "24 Pts."). */
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
