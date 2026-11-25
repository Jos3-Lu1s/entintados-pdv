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

