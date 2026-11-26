import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/app/screens/product_screen/tint_products_widget.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture(price) {
    class ProductScreen {
        async addProductToOrder() { this.nativeCalled = true; }
        showOptionalProductPopupIfNeeded() { this.optionalShown = true; }
    }
    vm.runInNewContext(source, {
        ProductScreen, TintPanel: {}, TintTable: {}, TintCreateColorPopup: {},
        resolvePresentationRange: () => ({ priceMin: 400, priceMax: 500 }),
        runTintFlow: async () => undefined,
        patch(target, extension) {
            Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        },
    });
    const screen = new ProductScreen();
    screen.tintUi = { tab: 'products', viewMode: 'list' };
    screen.env = { utils: { formatCurrency: String } };
    screen.order = { uiState: {} };
    screen.pos = {
        config: {}, requireCustomer: () => true, getOrder: () => screen.order,
        addLineToCurrentOrder: async (values) => { screen.created = values; return values; },
    };
    screen.product = { getPrice: () => price, tint_role: 'base', product_variant_ids: [{}] };
    return screen;
}

for (const [price, expected] of [[300, 400], [400, 400], [500, 500], [600, 500]]) {
    test(`price ${price} is displayed and added as ${expected}`, async () => {
        const screen = fixture(price);
        assert.equal(screen.tintRowPriceRange(screen.product).displayPrice, String(expected));
        await screen.addProductToOrder(screen.product);
        assert.equal(screen.created.price_unit, expected);
        assert.equal(screen.created.price_type, 'manual');
        assert.equal(screen.created.price_origin, 'manual');
        assert.equal(screen.optionalShown, true);
        assert.equal(screen.product.getPrice(), price);
    });
}

test('inside range uses native pricing', async () => {
    const screen = fixture(450);
    await screen.addProductToOrder(screen.product);
    assert.equal(screen.nativeCalled, true);
    assert.equal(screen.created, undefined);
});

test('grid and refunds keep native flow', async () => {
    for (const mode of ['grid', 'refund']) {
        const screen = fixture(300);
        if (mode === 'grid') screen.tintUi.viewMode = 'grid';
        else screen.order.preset_id = { is_return: true };
        await screen.addProductToOrder(screen.product);
        assert.equal(screen.nativeCalled, true);
        assert.equal(screen.created, undefined);
    }
});

test('cancelled product creation does not open optional products', async () => {
    const screen = fixture(600);
    screen.pos.addLineToCurrentOrder = async () => undefined;
    await screen.addProductToOrder(screen.product);
    assert.equal(screen.optionalShown, undefined);
});
