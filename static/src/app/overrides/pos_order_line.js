import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { isTintColorant, isTintedBase, tintDose, tintedProductName } from "@entintados_pdv/app/utils/tint_order";

/**
 * Parche para PosOrderline para:
 * 1. Asegurar cálculo consolidado de displayPrice con combo_line_ids.
 * 2. Sincronización reactiva de cantidades en bases entintadas y sus líneas hijas de colorante.
 * 3. Bloqueo de edición manual y protección de líneas hijas de colorantes.
 */
patch(PosOrderline.prototype, {
    getFullProductName() {
        return tintedProductName(this) || super.getFullProductName(...arguments);
    },

    setQuantity(quantity, keep_price = false) {
        if (isTintColorant(this) && !this._entintadosSyncingQuantity) {
            const notification =
                this.models?.env?.services?.notification ||
                this.env?.services?.notification ||
                this.order_id?.pos?.notification;
            if (notification) {
                notification.add(
                    _t(
                        "Los colorantes son componentes formulados y no se pueden modificar directamente."
                    ),
                    { type: "warning" }
                );
            }
            return false;
        }

        const isTinted = isTintedBase(this);

        if (isTinted) {
            const numQty =
                typeof quantity === "string"
                    ? quantity === "" || quantity === "remove"
                        ? 0
                        : parseFloat(quantity)
                    : Number(quantity);

            if (!isNaN(numQty)) {
                // Validación de enteros: las bases entintadas no se fraccionan en mostrador
                if (numQty !== 0 && !Number.isInteger(numQty)) {
                    const notification =
                        this.models?.env?.services?.notification ||
                        this.env?.services?.notification ||
                        this.order_id?.pos?.notification;
                    if (notification) {
                        notification.add(
                            _t(
                                "Las bases de pintura entintadas solo pueden venderse en unidades enteras (botes completos)."
                            ),
                            { type: "warning" }
                        );
                    }
                    return false;
                }
            }
        }

        const children = isTinted ? [...(this.combo_line_ids || [])] : [];
        for (const child of children) {
            child.unit_points ??= tintDose(child, this);
            child._entintadosSyncingQuantity = true;
        }
        let res;
        try {
            res = super.setQuantity(quantity, keep_price);
        } finally {
            for (const child of children) {
                delete child._entintadosSyncingQuantity;
            }
        }
        if (res !== true) {
            return res;
        }

        if (isTinted && this.combo_line_ids?.length) {
            const parentQty = Number(this.qty) || 0;
            for (const child of this.combo_line_ids) {
                if (isTintColorant(child) || child.unit_points !== undefined) {
                    const unitPoints = tintDose(child, this);
                    if (child.unit_points === undefined || child.unit_points === null) {
                        child.unit_points = unitPoints;
                    }
                    child.qty = unitPoints * parentQty;
                }
            }
        }

        if (!this._entintadosSyncingQuantity) {
            this.order_id?.refreshOrderPricing?.();
        }

        return res;
    },

    setUnitPrice(unitPrice) {
        if (isTintColorant(this)) {
            return false;
        }
        const pos = this.pos || this.order_id?.pos || this.models?.env?.services?.pos;
        if (this.fixed_price_locked && !this._entintadosSettingPrice) {
            const notification =
                this.models?.env?.services?.notification ||
                this.env?.services?.notification ||
                this.order_id?.pos?.notification ||
                pos?.notification;
            if (notification) {
                notification.add(
                    _t("El precio está pactado por acuerdo comercial y no se puede modificar."),
                    { type: "warning" }
                );
            }
            return false;
        }
        const canEdit = pos?.canEditUnitPrice ?? this.config?.user_can_edit_price ?? true;
        if (!canEdit && !this._entintadosSettingPrice) {
            const notification =
                this.models?.env?.services?.notification ||
                this.env?.services?.notification ||
                this.order_id?.pos?.notification ||
                pos?.notification;
            if (notification) {
                notification.add(
                    _t("No tienes permiso para modificar el precio de venta."),
                    { type: "warning" }
                );
            }
            return false;
        }
        return super.setUnitPrice(unitPrice);
    },

    setDiscount(discount) {
        if (isTintColorant(this)) {
            return false;
        }
        const pos = this.pos || this.order_id?.pos || this.models?.env?.services?.pos;
        if (this.fixed_price_locked && !this._entintadosSettingDiscount) {
            const notification =
                this.models?.env?.services?.notification ||
                this.env?.services?.notification ||
                this.order_id?.pos?.notification ||
                pos?.notification;
            if (notification) {
                notification.add(
                    _t("Los productos con precio fijo pactado no admiten descuento."),
                    { type: "warning" }
                );
            }
            return false;
        }
        const canEdit = pos?.canEditDiscount ?? this.config?.user_can_edit_discount ?? true;
        if (!canEdit && !this._entintadosSettingDiscount) {
            const notification =
                this.models?.env?.services?.notification ||
                this.env?.services?.notification ||
                this.order_id?.pos?.notification ||
                pos?.notification;
            if (notification) {
                notification.add(
                    _t("No tienes permiso para modificar el descuento."),
                    { type: "warning" }
                );
            }
            return false;
        }
        return super.setDiscount(discount);
    },

    canBeMergedWith(orderline) {
        if (
            isTintColorant(this) || isTintedBase(this) ||
            isTintColorant(orderline) || isTintedBase(orderline)
        ) {
            return false;
        }
        return super.canBeMergedWith(orderline);
    },

    delete() {
        if (
            isTintColorant(this) &&
            this.combo_parent_id &&
            !this.combo_parent_id._is_deleting &&
            (this.order_id?.lines?.includes?.(this.combo_parent_id) ||
                this.order_id?.orderlines?.includes?.(this.combo_parent_id))
        ) {
            const notification =
                this.models?.env?.services?.notification ||
                this.env?.services?.notification ||
                this.order_id?.pos?.notification;
            if (notification) {
                notification.add(
                    _t(
                        "No se puede eliminar un colorante individual. Para cambiar la fórmula, elimina la base entintada completa."
                    ),
                    { type: "warning" }
                );
            }
            return false;
        }

        const isTinted = isTintedBase(this);

        if (isTinted && this.combo_line_ids?.length && !this._is_deleting) {
            this._is_deleting = true;
            const children = [...this.combo_line_ids];
            for (const child of children) {
                if (child && !child._is_deleting) {
                    child._is_deleting = true;
                    child.delete?.() ?? this.order_id?.removeOrderline?.(child);
                }
            }
            this._is_deleting = false;
        }

        return super.delete();
    },

    get displayPrice() {
        const selfPrice =
            this.config.iface_tax_included === "total"
                ? this.priceIncl
                : this.priceExcl;

        if (!this.combo_line_ids?.length) {
            return selfPrice;
        }

        const comboSum = this.combo_line_ids.reduce((total, cl) => {
            const price =
                this.config.iface_tax_included === "total" ? cl.priceIncl : cl.priceExcl;
            return total + price;
        }, 0);

        return selfPrice + comboSum;
    },

    get displayPriceNoDiscount() {
        const selfPrice =
            this.config.iface_tax_included === "total"
                ? this.priceInclNoDiscount
                : this.priceExclNoDiscount;

        if (!this.combo_line_ids?.length) {
            return selfPrice;
        }

        const comboSum = this.combo_line_ids.reduce((total, cl) => {
            const price =
                this.config.iface_tax_included === "total"
                    ? cl.priceInclNoDiscount
                    : cl.priceExclNoDiscount;
            return total + price;
        }, 0);

        return selfPrice + comboSum;
    },
});

/**
 * Parche para PosOrder para:
 * 1. Redirigir la selección de líneas de colorante a la base padre.
 * 2. Asegurar eliminación en cascada de hijos de colorante al remover la línea base.
 */
patch(PosOrder.prototype, {
    selectOrderline(orderline) {
        if (isTintColorant(orderline) && orderline?.combo_parent_id) {
            return super.selectOrderline(orderline.combo_parent_id);
        }
        return super.selectOrderline(orderline);
    },

    removeOrderline(orderline) {
        if (orderline?.combo_line_ids?.length && !orderline._is_deleting) {
            orderline._is_deleting = true;
            const children = [...orderline.combo_line_ids];
            for (const child of children) {
                if (child && !child._is_deleting) {
                    child._is_deleting = true;
                    child.delete?.() ?? super.removeOrderline(child);
                }
            }
            orderline._is_deleting = false;
        }
        return super.removeOrderline(orderline);
    },
    serializeForORM(...args) {
        const data = super.serializeForORM(...args);
        const n = Number(this.uiState?.invoice_split_count || this.invoice_split_count);
        if (data && typeof data === "object" && Number.isInteger(n) && n >= 1) {
            data.invoice_split_count = n;
        }
        return data;
    },
});

patch(TicketScreen.prototype, {
    async onClickRefund() {
        this.dialog.add(AlertDialog, {
            title: _t("Reembolsos deshabilitados"),
            body: _t("La opción de reembolso está bloqueada en el punto de venta."),
        });
        return false;
    },
    async onDoRefund() {
        return this.onClickRefund();
    },
});

patch(ProductScreen.prototype, {
    getNumpadButtons() {
        const buttons = super.getNumpadButtons();
        const order = this.currentOrder || this.pos?.getOrder?.();
        const line = order?.getSelectedOrderline?.() || order?.get_selected_orderline?.();

        const canEditPrice = Boolean(this.pos?.canEditUnitPrice) && !line?.fixed_price_locked;
        const canEditDiscount = Boolean(this.pos?.canEditDiscount) && !line?.fixed_price_locked;

        if (!canEditPrice && this.pos?.numpadMode === "price") {
            this.pos.numpadMode = "quantity";
        }
        if (!canEditDiscount && this.pos?.numpadMode === "discount") {
            this.pos.numpadMode = "quantity";
        }

        return buttons.map((button) => {
            if (button.value === "price" && !canEditPrice) {
                return {
                    ...button,
                    disabled: true,
                    class: `${button.class || ""} disabled opacity-50`,
                };
            }
            if (button.value === "discount" && !canEditDiscount) {
                return {
                    ...button,
                    disabled: true,
                    class: `${button.class || ""} disabled opacity-50`,
                };
            }
            return button;
        });
    },

    onNumpadClick(buttonValue) {
        const order = this.currentOrder || this.pos?.getOrder?.();
        const line = order?.getSelectedOrderline?.() || order?.get_selected_orderline?.();
        if (buttonValue === "price") {
            if (line?.fixed_price_locked) {
                const notification = this.env?.services?.notification || this.pos?.notification;
                notification?.add(
                    _t("El precio está pactado por acuerdo comercial y no se puede modificar."),
                    { type: "warning" }
                );
                return;
            }
            if (!this.pos?.canEditUnitPrice) {
                const notification = this.env?.services?.notification || this.pos?.notification;
                notification?.add(
                    _t("No tienes permiso para modificar el precio de venta."),
                    { type: "warning" }
                );
                return;
            }
        }
        if (buttonValue === "discount") {
            if (line?.fixed_price_locked) {
                const notification = this.env?.services?.notification || this.pos?.notification;
                notification?.add(
                    _t("Los productos con precio fijo pactado no admiten descuento."),
                    { type: "warning" }
                );
                return;
            }
            if (!this.pos?.canEditDiscount) {
                const notification = this.env?.services?.notification || this.pos?.notification;
                notification?.add(
                    _t("No tienes permiso para modificar el descuento."),
                    { type: "warning" }
                );
                return;
            }
        }
        return super.onNumpadClick(buttonValue);
    },
});
