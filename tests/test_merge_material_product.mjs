import fs from 'node:fs';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('../static/src/fields/crm_material_product/merge_material_product.js', import.meta.url), 'utf8');
const { mergeMaterialProduct } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const list = { records: [], async delete(record) { this.records = this.records.filter((r) => r !== record); } };
function row(id, quantity = 1) {
    const record = { data: { product_id: id ? { id } : false, quantity },
        _parentRecord: { data: { material_line_ids: list } },
        async update(changes) { await Promise.resolve(); Object.assign(this.data, changes); } };
    list.records.push(record);
    return record;
}
const existing = row(10, 4);
const duplicate = row(false);
let nativeCalls = 0;
const nativeUpdate = async () => { nativeCalls++; };
await mergeMaterialProduct(duplicate, { id: 10 }, nativeUpdate);
assert.equal(existing.data.quantity, 5);
assert.equal(list.records.length, 1);
assert.equal(nativeCalls, 0);
const variant = row(false);
await mergeMaterialProduct(variant, { id: 11 }, nativeUpdate);
assert.equal(nativeCalls, 1);
assert.equal(existing.data.quantity, 5);
await mergeMaterialProduct(existing, { id: 10 }, nativeUpdate);
assert.equal(existing.data.quantity, 5, 'Reselecting the same field is not another order line');
const first = row(false), second = row(false);
await Promise.all([mergeMaterialProduct(first, { id: 10 }, nativeUpdate),
    mergeMaterialProduct(second, { id: 10 }, nativeUpdate)]);
assert.equal(existing.data.quantity, 7, 'Two selections must add two units');
assert.ok(!list.records.includes(first) && !list.records.includes(second));
const changed = row(12, 9);
await mergeMaterialProduct(changed, { id: 10 }, nativeUpdate);
assert.equal(existing.data.quantity, 8, 'Selecting a duplicate adds exactly one unit');
assert.ok(!list.records.includes(changed));
const rapid = row(false);
await Promise.all([mergeMaterialProduct(rapid, { id: 10 }, nativeUpdate),
    mergeMaterialProduct(rapid, { id: 10 }, nativeUpdate)]);
assert.equal(existing.data.quantity, 10);
assert.ok(list.records.includes(existing) && list.records.includes(variant));
console.log('PASS: selección repetida suma uno, conserva variantes y evita filas duplicadas y actualizaciones perdidas.');
