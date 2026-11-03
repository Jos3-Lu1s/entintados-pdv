import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const source = fs.readFileSync(new URL('../static/src/app/utils/customer_summary.js', import.meta.url), 'utf8');
const { customerSummary } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('separates discounts and rewards, groups tint and reconciles tax-inclusive total', () => {
    const color = { uuid: 'color', qty: 1, priceExclNoDiscount: 20, priceExcl: 18, combo_parent_id: {} };
    const base = { uuid: 'base', qty: 2, priceExclNoDiscount: 100, priceExcl: 90, combo_line_ids: [color] };
    const promo = { uuid: 'promo', qty: 1, priceExclNoDiscount: 50, priceExcl: 40, pricing_rule_type: 'promo' };
    const reward = { uuid: 'reward', qty: 1, priceExclNoDiscount: -8, priceExcl: -8, is_reward_line: true };
    const result = customerSummary({ lines: [base, color, promo, reward], priceExcl: 140,
        priceIncl: 162.4, totalDue: 162.4, amountTaxes: 22.4,
        prices: { taxDetails: { subtotals: [{ tax_groups: [{ group_name: 'IVA 16%', tax_amount_currency: 22.4 }] }] } } });
    assert.equal(result.rows.length, 2);
    assert.equal(result.rows[0].amount, 108);
    assert.equal(result.rows[0].unitPrice, 60);
    assert.equal(result.discounts, 12);
    assert.equal(result.promotions, 18);
    assert.equal(result.gross, 170);
    assert.equal(result.gross - result.discounts - result.promotions + result.tax + result.rounding, result.total);
    assert.equal(result.taxes[0].name, 'IVA 16%');
});

test('keeps free reward products visible and handles empty orders', () => {
    const order = { lines: [], prices: { taxDetails: {} }, priceExcl: 0, priceIncl: 0, totalDue: 0, amountTaxes: 0 };
    assert.deepEqual(customerSummary(order).rows, []);
    order.lines.push({ uuid: 'gift', qty: 1, is_reward_line: true, priceExcl: 0, priceExclNoDiscount: 0 });
    assert.equal(customerSummary(order).rows.length, 1);
});
