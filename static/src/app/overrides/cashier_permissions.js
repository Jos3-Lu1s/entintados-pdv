/** @odoo-module */
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { cashierEditRights } from "@entintados_pdv/app/utils/cashier_permissions";

/**
 * Permiso del cajero activo para teclear precio y descuento.
 *
 * Solo se revisa en las entradas del teclado (botones, `setLinePrice` y `setDiscountFromUI`):
 * los recálculos de tarifa, acuerdo, combo, entintado o promoción escriben sin pasar por aquí.
 * Este archivo se carga después de los demás parches de esas entradas, así que su revisión
 * corre primero y, si rechaza, no corre nada más.
 */

const PRICE_DENIED = () => _t("No tienes permiso para modificar el precio de venta.");
const DISCOUNT_DENIED = () => _t("No tienes permiso para modificar el descuento.");
const FIXED_PRICE_LOCKED = () =>
    _t("El precio está pactado por acuerdo comercial y no se puede modificar.");
const FIXED_PRICE_NO_DISCOUNT = () => _t("Los productos con precio fijo pactado no admiten descuento.");

/** Aviso que impide teclear `mode` ("price" o "discount") en `line`, o `null` si se permite. */
function keyboardBlockMessage(pos, mode, line) {
    if (mode === "price") {
        if (!pos.canEditUnitPrice) {
            return PRICE_DENIED();
        }
        if (line?.fixed_price_locked) {
            return FIXED_PRICE_LOCKED();
        }
    }
    if (mode === "discount") {
        if (!pos.canEditDiscount) {
            return DISCOUNT_DENIED();
        }
        if (line?.fixed_price_locked) {
            return FIXED_PRICE_NO_DISCOUNT();
        }
    }
    return null;
}

patch(PosStore.prototype, {
    get canEditUnitPrice() {
        return cashierEditRights(this.getCashier(), this.config).price;
    },

    get canEditDiscount() {
        return cashierEditRights(this.getCashier(), this.config).discount;
    },

    setCashier(...args) {
        const result = super.setCashier(...args);
        if (
            (this.numpadMode === "price" && !this.canEditUnitPrice) ||
            (this.numpadMode === "discount" && !this.canEditDiscount)
        ) {
            this.numpadMode = "quantity";
        }
        return result;
    },

    async setDiscountFromUI(line, val) {
        const message = keyboardBlockMessage(this, "discount", line);
        if (message) {
            this.notification.add(message, { type: "warning" });
            return;
        }
        return super.setDiscountFromUI(...arguments);
    },
});

patch(OrderSummary.prototype, {
    async setLinePrice(line, price) {
        const message = keyboardBlockMessage(this.pos, "price", line);
        if (message) {
            this.numberBuffer.reset();
            this.pos.notification.add(message, { type: "warning" });
            return;
        }
        return super.setLinePrice(...arguments);
    },
});

patch(ProductScreen.prototype, {
    getNumpadButtons() {
        const buttons = super.getNumpadButtons();
        const line = this.currentOrder?.getSelectedOrderline();
        const canEditPrice = this.pos.canEditUnitPrice && !line?.fixed_price_locked;
        const canEditDiscount = this.pos.canEditDiscount && !line?.fixed_price_locked;

        if (!canEditPrice && this.pos.numpadMode === "price") {
            this.pos.numpadMode = "quantity";
        }
        if (!canEditDiscount && this.pos.numpadMode === "discount") {
            this.pos.numpadMode = "quantity";
        }

        // Se suma al `disabled` del core (`manual_discount`, `restrict_price_control`, rol
        // `minimal`), nunca lo reemplaza.
        return buttons.map((button) => {
            const denied =
                (button.value === "price" && !canEditPrice) ||
                (button.value === "discount" && !canEditDiscount);
            if (!denied) {
                return button;
            }
            return {
                ...button,
                disabled: true,
                class: `${button.class || ""} disabled opacity-50`,
            };
        });
    },

    onNumpadClick(buttonValue) {
        const line = this.currentOrder?.getSelectedOrderline();
        const message = keyboardBlockMessage(this.pos, buttonValue, line);
        if (message) {
            this.pos.notification.add(message, { type: "warning" });
            return;
        }
        return super.onNumpadClick(buttonValue);
    },
});
