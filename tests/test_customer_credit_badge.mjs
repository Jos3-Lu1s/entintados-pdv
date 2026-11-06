import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/components/customer_credit_badge.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace('export class CustomerCreditBadge', 'class CustomerCreditBadge');
function fixture(call = async () => ({ credit_authorized: true, purchase_power: 1000, has_outstanding_invoices: false })) {
    class Screen {}
    const context = { Component: class {}, ProductScreen: Screen, PaymentScreen: Screen, PartnerLine: Screen,
        useState: (state) => state, useEffect() {} };
    vm.runInNewContext(`${source}\nglobalThis.Badge = CustomerCreditBadge;`, context);
    const badge = new context.Badge();
    badge.props = { partner: { id: 1 }, pos: { config: { id: 3 }, data: { call } }, orderUuid: 'one' };
    badge.setup();
    return badge;
}
test('credit is visible without any outstanding invoice or PPD history', async () => {
    const badge = fixture();
    assert.equal(badge.visible, false);
    await badge.refreshCredit();
    assert.equal(badge.visible, true);
    assert.equal(badge.state.available, 1000);
});
test('noncredit customers and exhausted limits have no badge', async () => {
    for (const info of [
        { credit_authorized: false, purchase_power: 1000 },
        { credit_authorized: true, purchase_power: 0 },
        { credit_authorized: true, purchase_power: -100 },
        { credit_authorized: true, purchase_power: 'invalid' },
    ]) {
        const badge = fixture(async () => info);
        await badge.refreshCredit();
        assert.equal(badge.visible, false);
    }
});
test('approval exceptions do not change the available balance displayed', async () => {
    const badge = fixture(async () => ({ credit_authorized: true, purchase_power: 400, risk_exception: true }));
    await badge.refreshCredit();
    assert.equal(badge.visible, true);
    assert.equal(badge.state.available, 400);
});
test('a late reply cannot display the previous customer credit', async () => {
    let reply;
    const badge = fixture(() => new Promise((resolve) => { reply = resolve; }));
    const pending = badge.refreshCredit();
    badge.props.partner = { id: 2 };
    reply({ credit_authorized: true, purchase_power: 1000 });
    await pending;
    assert.equal(badge.visible, false);
});
test('latest refresh wins even for the same customer', async () => {
    const replies = [];
    const badge = fixture(() => new Promise((resolve) => replies.push(resolve)));
    const older = badge.refreshCredit();
    const newer = badge.refreshCredit();
    replies[1]({ credit_authorized: true, purchase_power: 400 });
    await newer;
    replies[0]({ credit_authorized: true, purchase_power: 1000 });
    await older;
    assert.equal(badge.state.available, 400);
});
test('an unsuccessful refresh clears a previously displayed credit balance', async () => {
    const badge = fixture();
    await badge.refreshCredit();
    assert.equal(badge.visible, true);
    badge.props.pos.data.call = async () => { throw new Error('offline'); };
    await badge.refreshCredit();
    assert.equal(badge.visible, false);
});

test('customer list identifies credit customers without a balance or PPD invoices', () => {
    const xml = fs.readFileSync(new URL('../static/src/app/components/customer_credit_badge.xml', import.meta.url), 'utf8');
    const list = xml.slice(xml.indexOf('t-name="entintados_pdv.PartnerLineCreditBadge"'));
    assert.equal((list.match(/fa-credit-card/g) || []).length, 2);
    assert.equal(list.includes('<CustomerCreditBadge'), false);
    const conditions = [...list.matchAll(/t-if="([^"]+)"/g)].map((match) => match[1]);
    assert.equal(conditions.length, 2);
    for (const condition of conditions) {
        assert.equal(vm.runInNewContext(condition, { props: { partner: { is_credit: true, credit_limit: 0, total_due: 0 } } }), true);
        assert.equal(vm.runInNewContext(condition, { props: { partner: { is_credit: false, commercial_partner_id: { is_credit: true } } } }), true);
        assert.equal(Boolean(vm.runInNewContext(condition, { props: { partner: { is_credit: false, credit_limit: 5000, total_due: 500 } } })), false);
    }
});
