import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const source = fs.readFileSync(new URL('../static/src/fields/promo_hint/promo_hint_text.js', import.meta.url), 'utf8');
const { resolveLineRecord } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('resolves line record after save when record id or resId was updated in root.data.order_line.records', () => {
    const unsavedRecord = { id: 'datapoint_1', _virtualId: 'v1', resId: false };
    const root = {
        data: {
            order_line: {
                records: [
                    { id: 'datapoint_99', _virtualId: 'other', resId: 10 },
                    { id: 'datapoint_1', _virtualId: 'v1', resId: 42 },
                ],
            },
        },
    };
    const resolved = resolveLineRecord(root, unsavedRecord);
    assert.equal(resolved.resId, 42);
});

test('resolves newly created record when Odoo 19 StaticList recreates datapoints with new IDs', () => {
    const unsavedRecord = { id: 'datapoint_new', _virtualId: 'virtual_1', resId: false };
    const prevIds = new Set([10, 11]);
    const root = {
        data: {
            order_line: {
                records: [
                    { id: 'datapoint_recreated_1', _virtualId: false, resId: 10 },
                    { id: 'datapoint_recreated_2', _virtualId: false, resId: 11 },
                    { id: 'datapoint_recreated_3', _virtualId: false, resId: 99 },
                ],
            },
        },
    };
    const resolved = resolveLineRecord(root, unsavedRecord, prevIds, 2);
    assert.equal(resolved.resId, 99);
});


