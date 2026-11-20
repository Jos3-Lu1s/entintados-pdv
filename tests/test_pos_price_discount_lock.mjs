import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';

function patch(target, extension) {
    Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target),
        Object.getOwnPropertyDescriptors(target)));
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
}

// Mock base classes
class PosStore {
    setup() { return this; }
}

class PosOrderline {
    setUnitPrice(price) {
        this.price_unit = price;
        return true;
    }
    setDiscount(discount) {
        this.discount = discount;
        return true;
    }
}

// Load and patch PosStore
const storeSource = fs.readFileSync(new URL('../static/src/app/overrides/pos_store.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

new Function('patch', 'PosStore', 'PosOrder', 'getPartnerPricingRule', storeSource)(
    patch, PosStore, class PosOrder {}, () => ({ type: 'none' })
);

class ProductScreen {
    getNumpadButtons() {
        return [
            { value: "quantity", text: "Qty", class: "numpad-qty" },
            { value: "discount", text: "%", class: "numpad-discount" },
            { value: "price", text: "Price", class: "numpad-price" },
        ];
    }
    onNumpadClick(buttonValue) {
        return buttonValue;
    }
}

// Load and patch PosOrderline
const lineSource = fs.readFileSync(new URL('../static/src/app/overrides/pos_order_line.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

new Function(
    'patch', '_t', 'TicketScreen', 'ProductScreen', 'AlertDialog', 'PosOrderline', 'PosOrder',
    'isTintColorant', 'isTintedBase', 'tintDose', 'tintedProductName',
    lineSource
)(
    patch,
    (text) => text,
    class TicketScreen {},
    ProductScreen,
    class AlertDialog {},
    PosOrderline,
    class PosOrder {},
    (line) => Boolean(line.is_colorant),
    (line) => Boolean(line.is_tinted),
    () => 1,
    () => null
);

test('PosStore getters reflect pos.config user permissions', () => {
    const pos = new PosStore();
    
    // Default fallback when config has no flags
    pos.config = {};
    assert.equal(pos.canEditUnitPrice, true);
    assert.equal(pos.canEditDiscount, true);

    // Config with flags disabled
    pos.config = { user_can_edit_price: false, user_can_edit_discount: false };
    assert.equal(pos.canEditUnitPrice, false);
    assert.equal(pos.canEditDiscount, false);

    // Config with flags enabled
    pos.config = { user_can_edit_price: true, user_can_edit_discount: true };
    assert.equal(pos.canEditUnitPrice, true);
    assert.equal(pos.canEditDiscount, true);
});

test('PosOrderline blocks manual price edit when permission is denied', () => {
    const notifications = [];
    const pos = new PosStore();
    pos.config = { user_can_edit_price: false, user_can_edit_discount: true };
    pos.notification = {
        add(msg, opts) {
            notifications.push({ msg, opts });
        }
    };

    const line = new PosOrderline();
    line.pos = pos;
    line.price_unit = 100;

    // Cajero sin permiso intenta cambiar precio manualmente
    const result = line.setUnitPrice(120);
    assert.equal(result, false, 'Debe rechazar la edición de precio');
    assert.equal(line.price_unit, 100, 'El precio no debe alterarse');
    assert.equal(notifications.length, 1);
    assert.equal(notifications[0].msg, 'No tienes permiso para modificar el precio de venta.');
    assert.equal(notifications[0].opts?.type, 'warning');

    // Bypass del sistema (recálculo interno) debe permitirse
    line._entintadosSettingPrice = true;
    try {
        const bypassResult = line.setUnitPrice(150);
        assert.equal(bypassResult, true, 'El bypass del sistema debe permitirse');
        assert.equal(line.price_unit, 150);
    } finally {
        delete line._entintadosSettingPrice;
    }
});

test('PosOrderline blocks manual discount edit when permission is denied', () => {
    const notifications = [];
    const pos = new PosStore();
    pos.config = { user_can_edit_price: true, user_can_edit_discount: false };
    pos.notification = {
        add(msg, opts) {
            notifications.push({ msg, opts });
        }
    };

    const line = new PosOrderline();
    line.pos = pos;
    line.discount = 0;

    // Cajero sin permiso intenta cambiar descuento manualmente
    const result = line.setDiscount(15);
    assert.equal(result, false, 'Debe rechazar la edición de descuento');
    assert.equal(line.discount, 0, 'El descuento no debe alterarse');
    assert.equal(notifications.length, 1);
    assert.equal(notifications[0].msg, 'No tienes permiso para modificar el descuento.');
    assert.equal(notifications[0].opts?.type, 'warning');

    // Bypass del sistema (acuerdo o tarifa) debe permitirse
    line._entintadosSettingDiscount = true;
    try {
        const bypassResult = line.setDiscount(20);
        assert.equal(bypassResult, true, 'El bypass del sistema debe permitirse');
        assert.equal(line.discount, 20);
    } finally {
        delete line._entintadosSettingDiscount;
    }
});

test('Fixed price agreement is inviolable even when user has full permissions', () => {
    const notifications = [];
    const pos = new PosStore();
    // Usuario con todos los permisos
    pos.config = { user_can_edit_price: true, user_can_edit_discount: true };
    pos.notification = {
        add(msg, opts) {
            notifications.push({ msg, opts });
        }
    };

    const line = new PosOrderline();
    line.pos = pos;
    line.price_unit = 80;
    line.discount = 0;
    line.fixed_price_locked = true;

    // Intento de modificar precio en precio fijo pactado
    const priceResult = line.setUnitPrice(90);
    assert.equal(priceResult, false, 'Precio fijo pactado no permite cambio de precio');
    assert.equal(line.price_unit, 80);
    assert.equal(notifications[0].msg, 'El precio está pactado por acuerdo comercial y no se puede modificar.');

    // Intento de modificar descuento en precio fijo pactado
    const discountResult = line.setDiscount(10);
    assert.equal(discountResult, false, 'Precio fijo pactado no permite descuento');
    assert.equal(line.discount, 0);
    assert.equal(notifications[1].msg, 'Los productos con precio fijo pactado no admiten descuento.');
});

test('ProductScreen intercepts onNumpadClick for price and discount mode buttons', () => {
    const notifications = [];
    const pos = new PosStore();
    pos.config = { user_can_edit_price: false, user_can_edit_discount: false };
    pos.notification = {
        add(msg, opts) {
            notifications.push({ msg, opts });
        }
    };
    pos.numpadMode = "quantity";

    const screen = new ProductScreen();
    screen.pos = pos;
    screen.currentOrder = {
        getSelectedOrderline: () => ({ price_unit: 100, discount: 0 }),
    };

    // Al pulsar la tecla "price" en Numpad
    screen.onNumpadClick("price");
    assert.equal(pos.numpadMode, "quantity", "No debe cambiar el modo de numpad");
    assert.equal(notifications.length, 1);
    assert.equal(notifications[0].msg, "No tienes permiso para modificar el precio de venta.");

    // Al pulsar la tecla "discount" en Numpad
    screen.onNumpadClick("discount");
    assert.equal(pos.numpadMode, "quantity", "No debe cambiar el modo de numpad");
    assert.equal(notifications.length, 2);
    assert.equal(notifications[1].msg, "No tienes permiso para modificar el descuento.");

    // Con una línea de precio fijo pactado
    screen.currentOrder = {
        getSelectedOrderline: () => ({ price_unit: 100, discount: 0, fixed_price_locked: true }),
    };
    pos.config = { user_can_edit_price: true, user_can_edit_discount: true };

    screen.onNumpadClick("price");
    assert.equal(notifications.length, 3);
    assert.equal(notifications[2].msg, "El precio está pactado por acuerdo comercial y no se puede modificar.");

    screen.onNumpadClick("discount");
    assert.equal(notifications.length, 4);
    assert.equal(notifications[3].msg, "Los productos con precio fijo pactado no admiten descuento.");
});

test('ProductScreen disables price and discount buttons in getNumpadButtons', () => {
    const pos = new PosStore();
    pos.config = { user_can_edit_price: false, user_can_edit_discount: false };
    pos.numpadMode = "price";

    const screen = new ProductScreen();
    screen.pos = pos;
    screen.currentOrder = {
        getSelectedOrderline: () => ({ price_unit: 100, discount: 0 }),
    };

    let buttons = screen.getNumpadButtons();
    const priceBtn = buttons.find((b) => b.value === "price");
    const discBtn = buttons.find((b) => b.value === "discount");
    const qtyBtn = buttons.find((b) => b.value === "quantity");

    assert.equal(priceBtn.disabled, true, "El botón precio debe estar deshabilitado");
    assert.ok(priceBtn.class.includes("disabled"), "Debe tener clase disabled");
    assert.ok(priceBtn.class.includes("opacity-50"), "Debe tener clase opacity-50");

    assert.equal(discBtn.disabled, true, "El botón descuento debe estar deshabilitado");
    assert.ok(discBtn.class.includes("disabled"), "Debe tener clase disabled");
    assert.ok(discBtn.class.includes("opacity-50"), "Debe tener clase opacity-50");

    assert.equal(qtyBtn.disabled, undefined, "El botón cantidad debe permanecer habilitado");
    assert.equal(pos.numpadMode, "quantity", "El modo numpad debe resetearse a quantity si estaba en price");

    // Con permisos de usuario pero con línea de precio fijo pactado
    pos.config = { user_can_edit_price: true, user_can_edit_discount: true };
    screen.currentOrder = {
        getSelectedOrderline: () => ({ price_unit: 100, discount: 0, fixed_price_locked: true }),
    };
    buttons = screen.getNumpadButtons();
    const fixedPriceBtn = buttons.find((b) => b.value === "price");
    const fixedDiscBtn = buttons.find((b) => b.value === "discount");
    assert.equal(fixedPriceBtn.disabled, true, "Precio fijo bloquea botón de precio");
    assert.equal(fixedDiscBtn.disabled, true, "Precio fijo bloquea botón de descuento");

    // Con permisos de usuario y línea normal sin precio fijo
    screen.currentOrder = {
        getSelectedOrderline: () => ({ price_unit: 100, discount: 0, fixed_price_locked: false }),
    };
    buttons = screen.getNumpadButtons();
    const normalPriceBtn = buttons.find((b) => b.value === "price");
    const normalDiscBtn = buttons.find((b) => b.value === "discount");
    assert.equal(normalPriceBtn.disabled, undefined, "Precio debe estar habilitado con permisos");
    assert.equal(normalDiscBtn.disabled, undefined, "Descuento debe estar habilitado con permisos");
});
