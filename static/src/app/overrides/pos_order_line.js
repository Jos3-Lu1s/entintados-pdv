import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

/**
 * Parche para PosOrderline para:
 * 1. Asegurar cálculo consolidado de displayPrice con combo_line_ids.
 * 2. Sincronización reactiva de cantidades en bases entintadas y sus líneas hijas de colorante.
 * 3. Bloqueo de edición manual y protección de líneas hijas de colorantes.
 */
patch(PosOrderline.prototype, {
    setQuantity(quantity, keep_price = false) {
        if (this.is_tint_colorant) {
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

        const isTinted =
            this.is_tinted_base ||
            (this.combo_line_ids?.length && this.combo_line_ids.some((cl) => cl.is_tint_colorant));

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

        const res = super.setQuantity(quantity, keep_price);

        if (isTinted && this.combo_line_ids?.length) {
            const parentQty = Number(this.qty) || 0;
            for (const child of this.combo_line_ids) {
                if (child.is_tint_colorant || child.unit_points !== undefined) {
                    const unitPoints =
                        child.unit_points ?? (parentQty ? child.qty / parentQty : child.qty || 0);
                    if (child.unit_points === undefined || child.unit_points === null) {
                        child.unit_points = unitPoints;
                    }
                    child.qty = unitPoints * parentQty;
                }
            }
        }

        return res;
    },

    setUnitPrice(unitPrice) {
        if (this.is_tint_colorant) {
            return false;
        }
        return super.setUnitPrice(unitPrice);
    },

    setDiscount(discount) {
        if (this.is_tint_colorant) {
            return false;
        }
        return super.setDiscount(discount);
    },

    canBeMergedWith(orderline) {
        if (
            this.is_tint_colorant ||
            this.is_tinted_base ||
            orderline?.is_tint_colorant ||
            orderline?.is_tinted_base
        ) {
            return false;
        }
        return super.canBeMergedWith(orderline);
    },

    delete() {
        if (
            this.is_tint_colorant &&
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

        const isTinted =
            this.is_tinted_base ||
            (this.combo_line_ids?.length && this.combo_line_ids.some((cl) => cl.is_tint_colorant));

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
        if (orderline?.is_tint_colorant && orderline?.combo_parent_id) {
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
});
