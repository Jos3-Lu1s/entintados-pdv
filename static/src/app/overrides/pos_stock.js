import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { stockError, requiresPosStock } from "@entintados_pdv/app/utils/pos_stock";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";

patch(PosStore.prototype, {
    async addLineToOrder(vals, order, opts = {}, configure = true) {
        // Los colorantes hijos llegan en combo_line_ids de la base.
        if (this._isColorantLine(vals)) {
            this.notification.add(
                _t("Los colorantes solo se venden dentro de un producto entintado."),
                { type: "warning" }
            );
            return;
        }
        return super.addLineToOrder(vals, order, opts, configure);
    },

    _isColorantLine(vals) {
        const template = typeof vals.product_tmpl_id === "number"
            ? this.models["product.template"].get(vals.product_tmpl_id) : vals.product_tmpl_id;
        if (template) {
            return template.tint_role === "colorant";
        }
        const product = typeof vals.product_id === "number"
            ? this.models["product.product"].get(vals.product_id) : vals.product_id;
        return product?.product_tmpl_id?.tint_role === "colorant";
    },

    async refreshProductStock(products) {
        const tracked = products.filter(requiresPosStock);
        if (!tracked.length) {
            return;
        }
        const quantities = await this.data.call("product.product", "get_pos_stock", [
            [...new Set(tracked.map((product) => product.id))], this.config.id,
        ]);
        for (const product of tracked) {
            product.pos_stock_qty = quantities[product.id] ?? 0;
            const template = product.product_tmpl_id;
            if (template) {
                template.pos_stock_qty = template.product_variant_ids.reduce(
                    (total, variant) => total + (Number(variant.pos_stock_qty) || 0), 0);
            }
        }
    },

    async checkStockForPayment(order) {
        const lines = (order?.lines || []).filter((line) => line.qty > 0 && requiresPosStock(line.product_id));
        if (!lines.length) {
            return true;
        }
        try {
            await this.refreshProductStock(lines.map((line) => line.product_id));
        } catch {
            this.dialog.add(AlertDialog, {
                title: _t("No se pudieron consultar las existencias"),
                body: _t("Revisa la conexión antes de cobrar la venta."),
            });
            return false;
        }
        for (const line of lines) {
            const error = stockError(order, line.product_id, line.qty, line);
            if (error) {
                this.dialog.add(AlertDialog, { title: _t("Existencias insuficientes"), body: error });
                return false;
            }
        }
        return true;
    },
    async pay(...args) {
        const order = this.getOrder();
        if (!await this.checkStockForPayment(order) || order !== this.getOrder()) {
            return false;
        }
        return super.pay(...args);
    },
});

patch(PaymentScreen.prototype, {
    async addNewPaymentLine(...args) {
        if (!await this.pos.checkStockForPayment(this.currentOrder)) {
            return false;
        }
        return super.addNewPaymentLine(...args);
    },
    async sendPaymentRequest(...args) {
        if (!await this.pos.checkStockForPayment(this.currentOrder)) {
            return false;
        }
        return super.sendPaymentRequest(...args);
    },
});

patch(OrderPaymentValidation.prototype, {
    async isOrderValid(...args) {
        if (!await this.pos.checkStockForPayment(this.order)) {
            return false;
        }
        return super.isOrderValid(...args);
    },
});
