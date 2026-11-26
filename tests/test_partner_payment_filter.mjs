import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/overrides/partner_payment_filter.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture() {
    class ResPartner {
        get searchString() { return this.name || ''; }
    }
    class PartnerList {
        setup() {
            this.state = { query: '', loading: false, loadedPartners: [] };
            this.loadedPartnerIds = new Set();
        }
        getPartners(partners) {
            return partners.filter((p) => !this.state.query || p.name.includes(this.state.query));
        }
        _getSearchFields() { return ['name', 'vat']; }
        async getNewPartners() { this.nativeSearch = true; return []; }
    }
    vm.runInNewContext(source, { PartnerList, ResPartner, patch(target, extension) {
        Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
        Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
    } });
    const list = new PartnerList();
    list.setup();
    list.Partner = ResPartner;
    list.calls = [];
    list.results = [];
    list.pos = { config: { id: 7 }, data: { callRelated: async (...args) => {
        list.calls.push(JSON.parse(JSON.stringify(args)));
        return { 'res.partner': list.results };
    } } };
    return list;
}

test('filters credit customers without invoices and applies name search', () => {
    const list = fixture();
    const partners = [
        { id: 1, name: 'Ana', is_credit: true },
        { id: 2, name: 'Luis', is_credit: false },
        { id: 3, name: 'Ana sucursal', commercial_partner_id: { is_credit: true } },
        { id: 4, name: 'Pedro', pos_contact_info: { credit_authorized: true } },
    ];
    list.state.customerPaymentFilter = 'credit';
    assert.deepEqual(list.getPartners(partners).map(p => p.id), [1, 3, 4]);
    list.state.query = 'Ana';
    assert.deepEqual(list.getPartners(partners).map(p => p.id), [1, 3]);
    list.state.query = '';
    list.state.customerPaymentFilter = 'cash';
    assert.deepEqual(list.getPartners(partners).map(p => p.id), [2]);
});

test('remote search includes credit condition and keeps pagination separate', async () => {
    const list = fixture();
    list.results = [{ id: 10, name: 'Ana', is_credit: true }];
    list.state.query = 'Ana';
    await list.setCustomerPaymentFilter('credit');
    assert.deepEqual(list.calls[0], ['res.partner', 'get_new_partner', [7, [
        ['commercial_partner_id.is_credit', '=', true], '|', '|', ['name', 'ilike', 'Ana'], ['vat', 'ilike', 'Ana'], ['company_registry', 'ilike', 'Ana'],
    ], 0]]);
    await list.getNewPartners();
    assert.equal(list.calls[1][2][2], 1);
    assert.equal(list.state.loadedPartners.length, 1);
    await list.setCustomerPaymentFilter('cash');
    assert.equal(list.calls[2][2][2], 0);
    assert.equal(list.calls[2][2][1][0][2], false);
    await list.setCustomerPaymentFilter('cash');
    assert.equal(list.state.customerPaymentFilter, '');
    assert.equal(list.nativeSearch, true);
});

test('exhausted remote results stop requests without blocking another search', async () => {
    const list = fixture();
    await list.setCustomerPaymentFilter('credit');
    await list.getNewPartners();
    assert.equal(list.calls.length, 1);
    list.state.query = 'Otro';
    await list.getNewPartners();
    assert.equal(list.calls.length, 2);
});

test('company registry is searchable locally and remotely, including numeric values', async () => {
    const list = fixture();
    const partner = Object.assign(new list.Partner(), { name: 'Cliente', company_registry: '001234' });
    assert.ok(partner.searchString.includes('001234'));
    partner.company_registry = 'OTRO-99';
    assert.ok(partner.searchString.includes('OTRO-99'));
    assert.ok(!partner.searchString.includes('001234'));
    for (const query of ['001234', 'REG-55', 'reg@55']) {
        assert.ok(list._getSearchFields(query).includes('company_registry'));
    }
    list.state.query = '001234';
    await list.setCustomerPaymentFilter('credit');
    assert.ok(list.calls[0][2][1].some(item => Array.isArray(item) &&
        item[0] === 'company_registry' && item[2] === '001234'));
});
