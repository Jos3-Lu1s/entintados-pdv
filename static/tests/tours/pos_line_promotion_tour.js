import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as SelectionPopup from "@point_of_sale/../tests/generic_helpers/selection_popup_util";
import { registry } from "@web/core/registry";

const PRODUCT = "Esmalte Tour Promo";
const PROGRAM = "Promo Tour";

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

// Datos: `tests/test_pos_line_promotion_tour.py`. Cliente con 10 % de acuerdo en el producto y
// una promoción `specific` de 20 % en el mismo producto.
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
        ].flat(),
});
