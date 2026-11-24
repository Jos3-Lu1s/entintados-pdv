/* global posmodel */
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as Numpad from "@point_of_sale/../tests/generic_helpers/numpad_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as SelectionPopup from "@point_of_sale/../tests/generic_helpers/selection_popup_util";
import * as PosHr from "@pos_hr/../tests/tours/utils/pos_hr_helpers";
import { registry } from "@web/core/registry";

const PRODUCT = "Esmalte Permisos Tour";
const CUSTOMER = "Cliente Permisos Tour";
const CASHIER = "Cajero Sin Permiso Tour";
const CASHIER_PIN = "1357";
const SUPERVISOR = "Supervisor Permisos Tour";
const SUPERVISOR_PIN = "2468";

function numpadButtons(disabled) {
    const state = disabled ? "[disabled]" : ":not([disabled])";
    return ["Price", "%"].map((value) => ({
        content: `el botón '${value}' del teclado está ${disabled ? "desactivado" : "activo"}`,
        trigger: `div.numpad button${state}:contains(/^${value}$/)`,
    }));
}

function switchCashier(name, pin) {
    return [
        PosHr.clickCashierName(),
        SelectionPopup.has(name, { run: "click" }),
        PosHr.enterPin(pin),
    ].flat();
}

function badgeContains(text) {
    return {
        content: `el badge de la línea seleccionada dice '${text}'`,
        trigger: `.orderline.selected .pricing-rule-indicator:contains("${text}")`,
    };
}

function selectedLineHas(priceUnit, discount) {
    return {
        content: `la línea seleccionada tiene precio ${priceUnit} y descuento ${discount}`,
        trigger: `.orderline.selected:has(.product-name:contains("${PRODUCT}"))`,
        run: () => {
            const line = posmodel.getOrder().getSelectedOrderline();
            if (line.price_unit !== priceUnit || line.discount !== discount) {
                throw new Error(
                    `Se esperaba precio ${priceUnit} y descuento ${discount}; ` +
                        `hay ${line.price_unit} y ${line.discount}.`
                );
            }
        },
    };
}

// Datos: `tests/test_pos_cashier_permissions_tour.py`. Compañía con precio y descuento
// bloqueados; el supervisor tiene los dos grupos exentos y el cajero no tiene usuario.
registry.category("web_tour.tours").add("EntintadosPosCashierPermissionsTour", {
    steps: () =>
        [
            Chrome.clickBtn("Open Register"),
            PosHr.loginScreenIsShown(),
            PosHr.login(CASHIER, CASHIER_PIN),
            Dialog.confirm("Open Register"),

            // 1. Sin permiso: «Precio» y «%» desactivados.
            ProductScreen.clickDisplayedProduct(PRODUCT),
            ProductScreen.selectedOrderlineHas(PRODUCT, "1", "100.00"),
            numpadButtons(true),

            // 2. El supervisor teclea descuento y precio.
            switchCashier(SUPERVISOR, SUPERVISOR_PIN),
            numpadButtons(false),
            ProductScreen.clickNumpad("%"),
            ProductScreen.clickNumpad("7"),
            badgeContains("Manual · 7%"),
            ProductScreen.clickNumpad("Price"),
            ProductScreen.clickNumpad("8"),
            ProductScreen.clickNumpad("0"),
            selectedLineHas(80, 7),

            // 3. Con «%» activo, vuelve el cajero sin permiso: el teclado regresa a «Cantidad».
            ProductScreen.clickNumpad("%"),
            Numpad.isActive("%"),
            switchCashier(CASHIER, CASHIER_PIN),
            Numpad.isActive("Qty"),
            numpadButtons(true),
            selectedLineHas(80, 7),
            badgeContains("Manual · 7%"),

            // 4. El cliente con acuerdo de 10 % no pisa lo tecleado.
            ProductScreen.clickPartnerButton(),
            ProductScreen.clickCustomer(CUSTOMER),
            ProductScreen.clickLine(PRODUCT),
            selectedLineHas(80, 7),
            badgeContains("Manual · 7%"),

            // 5. Cobrar.
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
        ].flat(),
});
