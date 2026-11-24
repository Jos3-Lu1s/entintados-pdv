/* global posmodel */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as Notification from "@point_of_sale/../tests/generic_helpers/notification_util";
import * as Numpad from "@point_of_sale/../tests/generic_helpers/numpad_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as SelectionPopup from "@point_of_sale/../tests/generic_helpers/selection_popup_util";
import { registry } from "@web/core/registry";

const PRODUCT = "Esmalte Tour Promo";
const PROGRAM = "Promo Tour";
const SCHEME_PRODUCT = "Esmalte Esquema Tour";
const OTHER_SCHEME_PRODUCT = "Esmalte Otro Esquema Tour";
const TINT_BASE = "Base Tour Entintado";
const FIXED_COMBO = "Paquete Fijo Tour";
const PROMO_COMBO = "Paquete Promo Tour";
const COMBO_PROGRAM = "Esp Tour";

function badgeContains(text) {
    return [
        {
            content: `el badge de la línea seleccionada dice '${text}'`,
            trigger: `.orderline.selected .pricing-rule-indicator:contains("${text}")`,
        },
    ];
}

function clickStar() {
    return [
        {
            content: "abrir las promociones de la línea",
            trigger: ".orderline.selected .o-line-promo-star",
            run: "click",
        },
    ];
}

function pickInPopup(text) {
    return SelectionPopup.has(text, { run: "click" });
}

/** Marca la línea seleccionada como base entintada sin pasar por el flujo de entintado. */
function markSelectedAsTintedBase(productName) {
    return [
        {
            content: `marcar '${productName}' como base entintada`,
            trigger: `.orderline.selected:has(.product-name:contains("${productName}"))`,
            run: () => {
                posmodel.getOrder().getSelectedOrderline().is_tinted_base = true;
            },
        },
    ];
}

// Segunda fase: tarifa «por Esquema» de 10 % sobre el esquema del primer producto, cliente
// con acuerdo de 10 % en ese producto y descuento manual que sobrevive a la cantidad.
function schemePricelistAndManualDiscountSteps() {
    return [
        ReceiptScreen.clickNextOrder(),
        ProductScreen.clickDisplayedProduct(SCHEME_PRODUCT),
        ProductScreen.selectedOrderlineHas(SCHEME_PRODUCT, "1", "90.00"),
        ProductScreen.clickDisplayedProduct(OTHER_SCHEME_PRODUCT),
        ProductScreen.selectedOrderlineHas(OTHER_SCHEME_PRODUCT, "1", "100.00"),

        ProductScreen.clickPartnerButton(),
        ProductScreen.clickCustomer("Cliente Tour Promo"),
        ProductScreen.clickLine(SCHEME_PRODUCT),
        badgeContains("Producto · 10%"),

        ProductScreen.clickNumpad("%"),
        ProductScreen.clickNumpad("7"),
        badgeContains("Manual · 7%"),

        ProductScreen.clickNumpad("Qty"),
        ProductScreen.clickNumpad("3"),
        ProductScreen.selectedOrderlineHas(SCHEME_PRODUCT, "3"),
        badgeContains("Manual · 7%"),

        ProductScreen.clickDisplayedProduct(TINT_BASE),
        markSelectedAsTintedBase(TINT_BASE),
        Numpad.click("%"),
        Numpad.click("5"),
        Notification.has("El descuento de un entintado no se teclea"),
    ].flat();
}

function comboPackage(packageName) {
    const order = posmodel.getOrder();
    const packageLine = order.lines.find((line) => line.product_id.name === packageName);
    if (!packageLine) {
        throw new Error(`No está el paquete '${packageName}' en el pedido.`);
    }
    return packageLine;
}

/** Revisa en el modelo el precio y el badge de los productos de un paquete. */
function comboItemsHave(packageName, { prices, badge, ruleType }) {
    return [
        {
            content: `los productos de '${packageName}' tienen ${prices || ""} ${badge}`,
            trigger: `.orderline:has(.product-name:contains("${packageName}"))`,
            run: () => {
                const items = comboPackage(packageName).combo_line_ids;
                const actualPrices = items.map((line) => line.price_unit);
                if (prices && JSON.stringify(actualPrices) !== JSON.stringify(prices)) {
                    throw new Error(`Precios del combo: ${JSON.stringify(actualPrices)}`);
                }
                for (const line of items) {
                    if (line.pricing_rule_origin !== badge || line.pricing_rule_type !== ruleType) {
                        throw new Error(
                            `Badge del producto del combo: ${line.pricing_rule_type} '${line.pricing_rule_origin}'`
                        );
                    }
                    if (line.price_origin !== "combo") {
                        throw new Error(`Origen del precio del producto del combo: ${line.price_origin}`);
                    }
                }
            },
        },
    ];
}

/** Selecciona un producto del combo, como si el cajero lo tocara en la lista. */
function selectComboItem(packageName, index) {
    return [
        {
            content: `seleccionar el producto ${index + 1} de '${packageName}'`,
            trigger: `.orderline:has(.product-name:contains("${packageName}"))`,
            run: () => {
                const order = posmodel.getOrder();
                order.selectOrderline(comboPackage(packageName).combo_line_ids[index]);
            },
        },
    ];
}

// Tercera fase: combos. El cliente tiene precio fijo de 200 en un combo (se reparte entre sus
// productos) y 15 % en otro, que además recibe la promoción `specific` de 20 % del paquete.
function comboSteps() {
    return [
        Chrome.createFloatingOrder(),
        ProductScreen.clickPartnerButton(),
        ProductScreen.clickCustomer("Cliente Tour Promo"),

        ProductScreen.clickDisplayedProduct(FIXED_COMBO),
        comboItemsHave(FIXED_COMBO, {
            prices: [142.86, 42.86, 14.28],
            badge: "↳ Precio fijo",
            ruleType: "fixed_price",
        }),
        ProductScreen.totalAmountIs("200.00"),

        ProductScreen.clickDisplayedProduct(PROMO_COMBO),
        ProductScreen.clickLine(PROMO_COMBO),
        badgeContains("Producto · 15%"),
        comboItemsHave(PROMO_COMBO, {
            prices: [714.29, 214.29, 71.42],
            badge: "↳ Producto · 15%",
            ruleType: "product",
        }),

        clickStar(),
        pickInPopup(COMBO_PROGRAM),
        badgeContains(`${COMBO_PROGRAM} · 20%`),
        comboItemsHave(PROMO_COMBO, { badge: `↳ ${COMBO_PROGRAM} · 20%`, ruleType: "promo" }),

        selectComboItem(PROMO_COMBO, 1),
        Numpad.click("%"),
        Numpad.click("5"),
        Notification.has("es parte del combo"),
        comboItemsHave(PROMO_COMBO, { badge: `↳ ${COMBO_PROGRAM} · 20%`, ruleType: "promo" }),

        ProductScreen.clickLine(PROMO_COMBO),
        ProductScreen.totalAmountIs("1,000.00"),
        ProductScreen.clickPayButton(),
        PaymentScreen.clickPaymentMethod("Cash"),
        PaymentScreen.clickValidate(),
        ReceiptScreen.isShown(),
    ].flat();
}

// Datos: `tests/test_pos_line_promotion_tour.py`. Cliente con 10 % de acuerdo en el producto y
// una promoción `specific` de 20 % en el mismo producto; la segunda fase usa la tarifa por esquema
// y la tercera, dos combos con acuerdo en el paquete.
registry.category("web_tour.tours").add("EntintadosPosLinePromotionTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.clickPartnerButton(),
            ProductScreen.clickCustomer("Cliente Tour Promo"),
            ProductScreen.clickDisplayedProduct(PRODUCT),
            badgeContains("Producto"),

            clickStar(),
            pickInPopup(PROGRAM),
            badgeContains(`${PROGRAM} · 20%`),

            ProductScreen.clickNumpad("2"),
            ProductScreen.selectedOrderlineHas(PRODUCT, "2"),
            badgeContains(`${PROGRAM} · 20%`),

            clickStar(),
            pickInPopup("Sin promoción"),
            badgeContains("Producto"),

            clickStar(),
            pickInPopup(PROGRAM),
            badgeContains(`${PROGRAM} · 20%`),

            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),

            schemePricelistAndManualDiscountSteps(),
            comboSteps(),
        ].flat(),
});
