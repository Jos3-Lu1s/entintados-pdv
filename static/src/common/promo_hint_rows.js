/**
 * Filas del selector de promociones de una línea, en Ventas y en la caja: una por promoción disponible, la
 * aplicada marcada y, si la línea está en promoción, «Sin promoción» con el acuerdo al que vuelve.
 * Sin dependencias de Odoo: `translate` y `formatAmount` llegan como parámetros.
 *
 * @param {{options: {reward_id: number, name: string, target?: string, discount: number, difference: number,
 *          applied: boolean, scope: 'order'|'specific'}[],
 *          fallback: {label: string, difference: number, scope: 'order'|'specific'}|false,
 *          from_package?: boolean}|false} info  `from_package`: producto de un combo que solo
 *          informa la promoción de su paquete.
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
    // La promoción de pedido se aplica a todas las líneas, no solo a esta.
    const scopeLabel = (option) => (option.scope === "order" ? ` ${translate("(todo el pedido)")}` : "");
    const rows = (info.options || []).map((option) => {
        const targetPart = option.target ? ` · ${option.target}` : "";
        return {
            key: `reward-${option.reward_id}`,
            text: `${option.name}${targetPart} - ${option.discount} %${scopeLabel(option)}`,
            detail: option.applied
                ? (info.from_package ? translate("aplicada en el paquete") : translate("aplicada"))
                : describe(option.difference),
            rewardId: option.reward_id,
            applied: option.applied,
        };
    });
    if (info.fallback) {
        rows.push({
            key: "fallback",
            // La promoción de pedido es de todo o nada: se quita de todas las líneas.
            text: info.fallback.scope === "order"
                ? translate("Quitar la promoción del pedido")
                : translate("Sin promoción: vuelve a %(label)s").replace("%(label)s", info.fallback.label),
            detail: describe(info.fallback.difference),
            rewardId: false,
            applied: false,
        });
    }
    return rows;
}
