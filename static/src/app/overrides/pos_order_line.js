import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { isTintColorant, isTintedBase, tintDose } from "@entintados_pdv/app/utils/tint_order";

/**
 * Parche para PosOrderline para:
 * 1. Asegurar cálculo consolidado de displayPrice con combo_line_ids.
 * 2. Sincronización reactiva de cantidades en bases entintadas y sus líneas hijas de colorante.
 * 3. Bloqueo de edición manual y protección de líneas hijas de colorantes.
 */
patch(PosOrderline.prototype, {
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

        const order = this.order_id || this.order;
        if (order && typeof order._entintadosReconcileDiscounts === "function") {
            order._entintadosReconcileDiscounts();
        }

        return res;
    },

    setUnitPrice(unitPrice) {
        if (isTintColorant(this)) {
            return false;
        }
        return super.setUnitPrice(unitPrice);
    },

    setDiscount(discount) {
        if (isTintColorant(this)) {
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

    export_as_JSON() {
        const json = super.export_as_JSON ? super.export_as_JSON() : {};
        json.pricing_rule_type = this.pricing_rule_type || "none";
        json.pricing_rule_origin = this.pricing_rule_origin || "";
        json.pricing_rule_id = this.pricing_rule_id || false;
        return json;
    },

    init_from_JSON(json) {
        if (super.init_from_JSON) {
            super.init_from_JSON(json);
        }
        this.pricing_rule_type = json.pricing_rule_type || "none";
        this.pricing_rule_origin = json.pricing_rule_origin || "";
        this.pricing_rule_id = json.pricing_rule_id || false;
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
        console.log("[ENTINTADOS] serializeForORM n =", n);
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