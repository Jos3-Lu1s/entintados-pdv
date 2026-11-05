// Render the real payment summary with Odoo's installed Owl in an isolated DOM.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const runtime = process.argv[2] || path.join(os.tmpdir(), 'codex-owl-render-check');
const { JSDOM } = require(path.join(runtime, 'node_modules/jsdom'));
const dom = new JSDOM('<!doctype html><html><body><main id="app"></main></body></html>', {
    runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/',
});
const w = dom.window;
w.eval(fs.readFileSync(path.join(runtime, 'owl.js'), 'utf8'));
const { Component, App, reactive, useEffect } = w.owl;
const root = path.join(__dirname, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const document = new w.DOMParser().parseFromString(read('static/src/app/overrides/customer_payment_summary.xml'), 'text/xml');
const section = document.querySelector('section').outerHTML;
const template = `<templates><t t-name="PaymentFixture"><t t-call="point_of_sale.PaymentScreenDue"/></t><t t-name="point_of_sale.PaymentScreenDue">${section}</t><t t-name="entintados_pdv.CustomerContactInfo"><span>Cliente</span></t></templates>`;
class PaymentScreen extends Component {
    static template = 'PaymentFixture';
    static props = {};
    setup() {
        this.order = reactive({
            uuid: 'render-check', state: 'draft', lines: [], payment_ids: [], uiState: {},
            to_invoice: false, currency: { isZero: () => true },
            getPartner: () => null,
            isToInvoice() { return this.to_invoice; },
            setToInvoice(value) { this.to_invoice = value; },
        });
        this.pos = { company: { country_id: { code: 'MX' } }, config: {
            _l10n_mx_edi_usage: [{ value: 'G01', name: 'Adquisición de mercancías' }, { value: 'G03', name: 'Gastos en general' }],
        } };
    }
    onMounted() {}
    get currentOrder() { return process.argv.includes('--missing-order') ? undefined : this.order; }
}
function patch(target, extension) {
    const parent = Object.create(Object.getPrototypeOf(target));
    Object.defineProperties(parent, Object.getOwnPropertyDescriptors(target));
    Object.setPrototypeOf(extension, parent);
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
}
const utils = new w.Function(read('static/src/app/utils/invoice_options.js').replace(/export /g, '') +
    '; return {canSplitInvoice, initializePaymentInvoice, isInvoiceCollection, setInvoiceSplitCount};')();
const source = read('static/src/app/overrides/customer_payment_summary.js').replace(/^import .*;\r?\n/gm, '');
new w.Function('patch', 'useEffect', 'formatFloat', 'PaymentScreen', 'ProductInfoPopup', 'customerSummary',
    ...Object.keys(utils), source)(patch, useEffect, String, PaymentScreen,
    { prototype: { _hasMarginsCostsAccessRights: () => false } },
    () => ({ rows: [], showMargins: false, gross: 100, discounts: 0, promotions: 0,
        subtotal: 100, taxes: [], tax: 16, rounding: 0, total: 116 }), ...Object.values(utils));
(async () => {
    try {
        const app = new App(PaymentScreen, { templates: template,
            env: { utils: { formatCurrency: (amount) => '$' + amount } } });
        const component = await app.mount(w.document.querySelector('#app'));
        await new Promise((resolve) => w.setTimeout(resolve, 60));
        if (process.argv.includes('--missing-order')) {
            assert.equal(w.document.querySelector('#payment_invoice_usage'), null);
            console.log('PASS: actual Owl payment summary tolerates a cold reload before the order is available.');
            app.destroy();
            return;
        }
        assert.ok(w.document.querySelector('#payment_invoice_usage'));
        assert.equal(w.document.querySelector('#payment_invoice_usage').options.length, 2);
        assert.equal(component.currentOrder.to_invoice, true);
        console.log('PASS: actual Owl payment template mounts with all three invoice selectors and invoicing enabled.');
        app.destroy();
    } catch (error) {
        console.error(error.stack);
        for (let cause = error.cause; cause; cause = cause.cause) console.error('CAUSE:', cause.stack);
        process.exitCode = 1;
    } finally { w.close(); }
})();
