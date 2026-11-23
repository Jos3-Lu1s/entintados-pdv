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

// Datos: `tests/test_pos_line_promotion_tour.py`. Cliente con 10 % de acuerdo en el producto y
// una promoción `specific` de 20 % en el mismo producto; la segunda fase usa la tarifa por esquema.
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
        ].flat(),
});
