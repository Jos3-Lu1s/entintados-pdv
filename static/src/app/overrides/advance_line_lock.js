import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";

const lockedMessage = () => _t("Las líneas de anticipo no se pueden editar desde el ticket.");

patch(PosOrderline.prototype, {
    get isAdvanceLocked() {
        return Boolean(this.product_id?.id && (
            this.advance_source_ref || this.product_id.product_tmpl_id?.is_accounting_advance ||
            this.product_id.id === this.config?.down_payment_product_id?.id
        ));
    },
    setQuantity(value, ...args) {
        if (this.isAdvanceLocked && Number(value) !== Number(this.qty)) {
            return { title: _t("Anticipo protegido"), body: lockedMessage() };
        }
        return super.setQuantity(value, ...args);
    },
    setUnitPrice(value, ...args) {
        if (this.isAdvanceLocked && Number(value) !== Number(this.price_unit)) {
            return false;
        }
        return super.setUnitPrice(value, ...args);
    },
    setDiscount(value, ...args) {
        if (this.isAdvanceLocked && Number(value) !== Number(this.discount || 0)) {
            return false;
        }
        return super.setDiscount(value, ...args);
    },
    setNote(...args) {
        if (this.isAdvanceLocked) return false;
        return super.setNote(...args);
    },
    setCustomerNote(...args) {
        if (this.isAdvanceLocked) return false;
        return super.setCustomerNote(...args);
    },
    canBeMergedWith(line) {
        if (this.isAdvanceLocked || line?.isAdvanceLocked) return false;
        return super.canBeMergedWith(line);
    },
});

patch(PosOrder.prototype, {
    removeOrderline(line) {
        const advance = line?.isAdvanceLocked;
        const result = super.removeOrderline(line);
        if (advance && !this.lines.includes(line)) {
            // Preserve the user's choice on this ticket when revisiting payment.
            this.uiState.skipAutomaticCustomerAdvance = true;
        }
        return result;
    },
});

patch(OrderSummary.prototype, {
    _blockAdvanceEdit(line) {
        if (!line?.isAdvanceLocked) return false;
        this.numberBuffer.reset();
        const notification = this.pos.notification || this.env?.services?.notification;
        notification?.add(lockedMessage(), { type: "warning" });
        return true;
    },
    async updateSelectedOrderline(...args) {
        const line = this.currentOrder.getSelectedOrderline();
        if (line?.isAdvanceLocked && args[0]?.key === "Backspace") {
            this.numberBuffer.reset();
            return this.currentOrder.removeOrderline(line);
        }
        if (this._blockAdvanceEdit(this.currentOrder.getSelectedOrderline())) return;
        return await super.updateSelectedOrderline(...args);
    },
    _setValue(...args) {
        const line = this.currentOrder.getSelectedOrderline();
        if (line?.isAdvanceLocked && args[0] === "remove") {
            this.numberBuffer.reset();
            return this.currentOrder.removeOrderline(line);
        }
        if (this._blockAdvanceEdit(this.currentOrder.getSelectedOrderline())) return;
        return super._setValue(...args);
    },
    async onOrderlineLongPress(event, line) {
        if (this._blockAdvanceEdit(line)) return false;
        return await super.onOrderlineLongPress(event, line);
    },
    async setLinePrice(line, ...args) {
        if (this._blockAdvanceEdit(line)) return;
        return await super.setLinePrice(line, ...args);
    },
    async editPackLotLines(line) {
        if (this._blockAdvanceEdit(line)) return;
        return await super.editPackLotLines(line);
    },
});

patch(ProductScreen.prototype, {
    getNumpadButtons() {
        const buttons = super.getNumpadButtons();
        if (!this.currentOrder?.getSelectedOrderline()?.isAdvanceLocked) return buttons;
        return buttons.map((button) => ({
            ...button,
            disabled: button.value === "Backspace" ? Boolean(button.disabled) : true,
        }));
    },
});
