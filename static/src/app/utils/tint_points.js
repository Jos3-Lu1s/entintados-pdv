
/**
 * Utilidades para el cálculo y formato de puntos de colorante en notación mixta (Y/Pts).
 */

/** Puntos por onza de colorante (48 puntos = 1Y). */
export const POINTS_PER_OUNCE = 48;

/** Símbolo que denota las onzas de colorante. */
export const OUNCE_SYMBOL = "Y";

function cleanNum(n) {
    return Number(Math.round(n * 10000) / 10000).toString();
}

/** Descompone puntos totales en onzas (Y) y puntos sobrantes. */
export function splitPoints(points) {
    const total = Number(points || 0);
    const sign = total < 0 ? -1 : 1;
    const abs = Math.abs(total);
    const ounces = Math.floor(abs / POINTS_PER_OUNCE);
    const rest = Number((abs % POINTS_PER_OUNCE).toFixed(4));
    return [sign * ounces, sign * rest];
}

/** Formatea puntos a texto en notación tradicional (ej. "9Y 24", "9Y 24.5", "2Y", "24 Pts."). */
export function formatPoints(points) {
    const total = Number(points || 0);
    if (total < 0) {
        return `-${formatPoints(-total)}`;
    }
    const [ounces, rest] = splitPoints(total);
    const restVal = Math.abs(rest);
    if (ounces && restVal > 0) {
        return `${ounces}${OUNCE_SYMBOL} ${cleanNum(restVal)}`;
    }
    if (ounces) {
        return `${ounces}${OUNCE_SYMBOL}`;
    }
    return `${cleanNum(restVal)} Pts.`;
}
