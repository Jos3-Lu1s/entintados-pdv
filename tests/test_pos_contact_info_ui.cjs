// Render the actual Owl template with a fixture; no sale or contact is changed.
const { chromium } = require(process.argv[2] || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
    const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' });
    try {
        const page = await browser.newPage();
        await page.goto('http://localhost:8069/web/login');
        await page.setContent('<main id="fixture"></main>');
        await page.addScriptTag({ url: 'http://localhost:8069/web/static/lib/owl/owl.js' });
        const templates = fs.readFileSync(path.join(__dirname,
            '../static/src/app/components/customer_contact_info.xml'), 'utf8');
        await page.evaluate(async (templates) => {
            class ContactCard extends owl.Component {
                static template = 'entintados_pdv.CustomerContactInfo';
                static props = [];
                partner = {
                    name: 'Cliente <prueba>', pos_contact_info: {
                        reference: '7804', vat: 'XAXX010101000', salesperson: 'PV MATRIZ',
                        address: 'Calle principal 10, Puebla', fiscal_regime: 'Régimen de prueba',
                        invoice: { name: 'Oficina fiscal', address: 'Calle fiscal 20' },
                        delivery: { name: 'Bodega cliente', address: 'Calle entrega 30' },
                    },
                };
            }
            await new owl.App(ContactCard, { templates }).mount(document.querySelector('#fixture'));
        }, templates);
        assert.equal(await page.locator('.o-pos-contact-info strong').first().textContent(), 'Cliente <prueba>');
        assert.equal(await page.locator('details').evaluate(el => el.open), false);
        await page.locator('summary').click();
        assert.equal(await page.locator('details').evaluate(el => el.open), true);
        assert.match(await page.locator('details').innerText(), /Calle fiscal 20/);
        assert.match(await page.locator('details').innerText(), /Calle entrega 30/);
        console.log('Owl contact card: renders, escapes text and expands contact addresses correctly.');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
