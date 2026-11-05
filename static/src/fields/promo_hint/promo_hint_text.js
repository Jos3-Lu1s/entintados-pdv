/**
 * Filas del selector de promociones de una línea de venta: una por promoción disponible, la
 * aplicada marcada y, si la línea está en promoción, «Sin promoción» con el acuerdo al que vuelve.
 * Sin dependencias de Odoo: `translate` y `formatAmount` llegan como parámetros.
 *
 * @param {{options: {reward_id: number, name: string, discount: number, difference: number,
 *          applied: boolean}[], fallback: {label: string, difference: number}|false}|false} info
 * @param {(text: string) => string} translate
 * @param {(amount: number) => string} formatAmount
 * @returns {{key: string, text: string, detail: string, rewardId: number|false, applied: boolean}[]}
 */
export function promoHintLines(info, translate, formatAmount) {
    if (!info) {
        return [];
    }
    const describe = (difference) => {
        if (difference > 0) {
            return translate("ahorra %(amount)s más").replace("%(amount)s", formatAmount(difference));
        }
        if (difference < 0) {
            return translate("da %(amount)s menos que el descuento actual")
                .replace("%(amount)s", formatAmount(-difference));
        }
        return translate("da lo mismo que el descuento actual");
    };
    const rows = (info.options || []).map((option) => ({
        key: `reward-${option.reward_id}`,
        text: `${option.name} - ${option.discount} %`,
        detail: option.applied ? translate("aplicada") : describe(option.difference),
        rewardId: option.reward_id,
        applied: option.applied,
    }));
    if (info.fallback) {
        rows.push({
            key: "fallback",
            text: translate("Sin promoción: vuelve a %(label)s").replace("%(label)s", info.fallback.label),
            detail: describe(info.fallback.difference),
            rewardId: false,
            applied: false,
        });
    }
    return rows;
}
