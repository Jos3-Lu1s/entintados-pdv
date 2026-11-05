import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const source = fs.readFileSync(new URL('../static/src/fields/promo_hint/promo_hint_text.js', import.meta.url), 'utf8');
const { promoHintLines } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

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

test('handles a line without options', () => {
    assert.deepEqual(promoHintLines(false, identity, money), []);
    assert.deepEqual(promoHintLines({ options: [], fallback: false }, identity, money), []);
});
