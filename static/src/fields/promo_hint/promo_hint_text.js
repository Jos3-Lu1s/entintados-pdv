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

/**
 * Ubica la línea actualizada en `root.data.order_line.records` tras un guardado de formulario,
 * preservando la referencia correcta de `resId` aun si la fila se agregó en memoria.
 *
 * @param {object} root Record raíz del formulario Odoo
 * @param {object} record Registro de la línea del componente
 * @param {Set<number>} [prevIds] Conjunto de resIds que existían antes de guardar
 * @param {number} [lineIndex] Posición de la fila en la lista
 * @returns {object} Registro actualizado en la colección de líneas, o `record` original si no se encuentra
 */
export function resolveLineRecord(root, record, prevIds = new Set(), lineIndex = -1) {
    if (!root?.data?.order_line?.records || !record) {
        return record;
    }
    const records = root.data.order_line.records;
    if (record.resId) {
        const found = records.find((r) => r.resId === record.resId);
        if (found) {
            return found;
        }
    }
    if (record.id) {
        const found = records.find((r) => r.id === record.id);
        if (found) {
            return found;
        }
    }
    if (record._virtualId) {
        const found = records.find((r) => r._virtualId === record._virtualId);
        if (found) {
            return found;
        }
    }
    if (prevIds && prevIds.size > 0) {
        const newlyCreated = records.find((r) => r.resId && !prevIds.has(r.resId));
        if (newlyCreated) {
            return newlyCreated;
        }
    }
    if (lineIndex >= 0 && lineIndex < records.length) {
        return records[lineIndex];
    }
    return record;
}

