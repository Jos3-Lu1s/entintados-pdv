const queues = new WeakMap();

/** Repeated selections add exactly one unit to the earlier demonstration row. */
export function mergeMaterialProduct(record, value, nativeUpdate, options = {}, queueKey) {
    const list = record._parentRecord?.data?.material_line_ids;
    if (!list) {
        return nativeUpdate(value, options);
    }
    const key = queueKey || list;
    const pending = (queues.get(key) || Promise.resolve()).catch(() => {}).then(async () => {
        const id = value?.id ?? value?.[0];
        const currentId = record.data.product_id?.id ?? record.data.product_id?.[0];
        const existing = id && id !== currentId && list.records.find((other) =>
            other !== record && (other.data.product_id?.id ?? other.data.product_id?.[0]) === id);
        if (!existing) {
            return nativeUpdate(value, options);
        }
        await existing.update({ quantity: (Number(existing.data.quantity) || 0) + 1 });
        const current = list.records.find((row) => row === record || (record.id && row.id === record.id));
        if (current) {
            await list.delete(current);
        }
    });
    queues.set(key, pending);
    return pending;
}
