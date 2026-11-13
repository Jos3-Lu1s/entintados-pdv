import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const source = fs.readFileSync(new URL('../static/src/fields/promo_hint/promo_hint_text.js', import.meta.url), 'utf8');
const { promoHintLines, resolveLineRecord } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const identity = (text) => text;
const money = (amount) => `$ ${amount.toFixed(2)}`;

test('describes savings, losses and ties against the current discount', () => {
    const rows = promoHintLines({
        options: [
            { reward_id: 1, name: 'Promo TK', discount: 20, difference: 30, applied: false },
            { reward_id: 2, name: 'Promo TK', discount: 5, difference: -15, applied: false },
            { reward_id: 3, name: 'Promo TK', discount: 10, difference: 0, applied: false },
        ],
        fallback: false,
    }, identity, money);
    assert.deepEqual(rows.map((row) => [row.text, row.detail, row.rewardId]), [
        ['Promo TK - 20 %', 'ahorra $ 30.00 más', 1],
        ['Promo TK - 5 %', 'da $ 15.00 menos que el descuento actual', 2],
        ['Promo TK - 10 %', 'da lo mismo que el descuento actual', 3],
    ]);
});

test('marks the applied reward and offers to return to the agreement', () => {
    const rows = promoHintLines({
        options: [
            { reward_id: 1, name: 'Promo TK', discount: 20, difference: 0, applied: true },
            { reward_id: 2, name: 'Promo TK', discount: 10, difference: -10, applied: false },
        ],
        fallback: { label: 'Desc. Producto (10.0%)', difference: -10 },
    }, identity, money);
    assert.equal(rows[0].applied, true);
    assert.equal(rows[0].detail, 'aplicada');
    assert.deepEqual(rows.at(-1), {
        key: 'fallback',
        text: 'Sin promoción: vuelve a Desc. Producto (10.0%)',
        detail: 'da $ 10.00 menos que el descuento actual',
        rewardId: false,
        applied: false,
    });
});

test('labels order-wide promotions and leaves product promotions unchanged', () => {
    const rows = promoHintLines({
        options: [
            { reward_id: 1, name: 'Promo Esp', discount: 20, difference: 20, applied: false, scope: 'specific' },
            { reward_id: 2, name: 'Promo Pedido', discount: 10, difference: 10, applied: false, scope: 'order' },
        ],
        fallback: false,
    }, identity, money);
    assert.deepEqual(rows.map((row) => row.text), [
        'Promo Esp - 20 %',
        'Promo Pedido - 10 % (todo el pedido)',
    ]);
});

test('offers to remove the order-wide promotion from the whole order', () => {
    const rows = promoHintLines({
        options: [
            { reward_id: 2, name: 'Promo Pedido', discount: 10, difference: 0, applied: true, scope: 'order' },
        ],
        fallback: { label: 'Desc. Producto (15.0%)', difference: 5, scope: 'order' },
    }, identity, money);
    assert.deepEqual(rows.at(-1), {
        key: 'fallback',
        text: 'Quitar la promoción del pedido',
        detail: 'ahorra $ 5.00 más',
        rewardId: false,
        applied: false,
    });
});

test('describes the promotion a combo product takes from its package', () => {
    const rows = promoHintLines({
        options: [
            { reward_id: 4, name: 'Promo Esp', discount: 10, difference: 0, applied: true, scope: 'specific' },
        ],
        fallback: false,
        from_package: true,
    }, identity, money);
    assert.deepEqual(rows, [{
        key: 'reward-4',
        text: 'Promo Esp - 10 %',
        detail: 'aplicada en el paquete',
        rewardId: 4,
        applied: true,
    }]);
});

test('handles a line without options', () => {
    assert.deepEqual(promoHintLines(false, identity, money), []);
    assert.deepEqual(promoHintLines({ options: [], fallback: false }, identity, money), []);
});

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


