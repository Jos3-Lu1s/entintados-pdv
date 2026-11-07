import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const read = (path) => fs.readFileSync(new URL(path, import.meta.url), 'utf8');
const list = read('../static/src/app/components/customer_credit_badge.xml');
const contact = read('../static/src/app/components/customer_contact_info.xml');
const conditionFor = (xml) => [...xml.matchAll(/t-if="([^"]*is_credit[^"]*)"/g)]
    .map((match) => match[1].replace(/\bor\b/g, '||').replace(/\band\b/g, '&&'));

test('credit condition renders the contact icon in both list layouts and the selected customer', () => {
    assert.equal((list.match(/fa-credit-card/g) || []).length, 2);
    assert.equal((contact.match(/fa-credit-card/g) || []).length, 1);
    assert.equal(conditionFor(list).length, 2);
    assert.equal(conditionFor(contact).length, 1);
    for (const condition of [...conditionFor(list), ...conditionFor(contact)]) {
        for (const partner of [
            { is_credit: true, credit_limit: 0, total_due: 0 },
            { is_credit: true, credit_limit: 1000, total_due: 2000 },
            { is_credit: false, commercial_partner_id: { is_credit: true } },
        ]) {
            assert.equal(Boolean(vm.runInNewContext(condition, { props: { partner }, partner })), true);
        }
        const partner = { is_credit: false, credit_limit: 5000, total_due: 500 };
        assert.equal(Boolean(vm.runInNewContext(condition, { props: { partner }, partner })), false);
    }
});
test('credit identification needs no extra Owl component, RPC or hidden balance column', () => {
    assert.equal(list.includes('<CustomerCreditBadge'), false);
    assert.equal(contact.includes('<CustomerCreditBadge'), false);
    assert.equal(list.includes('partner-line-balance'), false);
    assert.equal(read('../__manifest__.py').includes('customer_credit_badge.js'), false);
});
