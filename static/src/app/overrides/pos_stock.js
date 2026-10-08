import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { stockError, requiresPosStock } from "@entintados_pdv/app/utils/pos_stock";
import { Mutex } from "@web/core/utils/concurrency";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";

const stockMutexes = new WeakMap();
let stockNotification;

patch(PosStore.prototype, {
    async addLineToOrder(vals, order, opts = {}, configure = true) {
        let mutex = stockMutexes.get(this);
        if (!mutex) {
            mutex = new Mutex();
            stockMutexes.set(this, mutex);
        }
        return mutex.exec(async () => {
            stockNotification = this.notification;
            const template = typeof vals.product_tmpl_id === "number"
                ? this.models["product.template"].get(vals.product_tmpl_id) : vals.product_tmpl_id;
            const explicitProduct = typeof vals.product_id === "number"
                ? this.models["product.product"].get(vals.product_id) : vals.product_id;
            const products = explicitProduct ? [explicitProduct] : template?.product_variant_ids || [];
            const qty = vals.qty ?? (order.preset_id?.is_return ? -1 : 1);
            if (qty > 0 && products.some(requiresPosStock)) {
                try {
                    await this.refreshProductStock(products);
                } catch {
                    this.dialog.add(AlertDialog, {
                        title: "No se pudieron consultar las existencias",
                        body: "Revisa la conexión antes de vender este producto.",
                    });
                    return;
                }
                const errors = products.map((product) => stockError(order, product, qty));
                if (errors.length && errors.every(Boolean)) {
                    this.notification.add(errors[0], { type: "warning" });
                    return;
                }
            }
            return super.addLineToOrder(vals, order, opts, configure);
        });
    },

    async handleConfigurableProduct(values, template, opts, configure) {
        const result = await super.handleConfigurableProduct(values, template, opts, configure);
        if (result === false) {
            return false;
        }
        if (requiresPosStock(values.product_id) && values.qty > 0) {
            try {
                await this.refreshProductStock([values.product_id]);
            } catch {
                this.dialog.add(AlertDialog, {
                    title: "No se pudieron consultar las existencias",
                    body: "Revisa la conexión antes de vender este producto.",
                });
                return false;
            }
            const error = stockError(values.order_id, values.product_id, values.qty);
            if (error) {
                this.dialog.add(AlertDialog, { title: "Existencias insuficientes", body: error });
                return false;
            }
        }
        return result;
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

});

patch(OrderPaymentValidation.prototype, {
    async isOrderValid(...args) {
        const lines = this.order.lines.filter((line) => line.qty > 0 && requiresPosStock(line.product_id));
        try {
            await this.pos.refreshProductStock(lines.map((line) => line.product_id));
        } catch {
            this.pos.dialog.add(AlertDialog, {
                title: "No se pudieron consultar las existencias",
                body: "Revisa la conexión antes de confirmar la venta.",
            });
            return false;
        }
        for (const line of lines) {
            const error = stockError(this.order, line.product_id, line.qty, line);
            if (error) {
                this.pos.dialog.add(AlertDialog, { title: "Existencias insuficientes", body: error });
                return false;
            }
        }
        return super.isOrderValid(...args);
    },
});

patch(PosOrderline.prototype, {
    merge(line) {
        // Ambas líneas siguen en el ticket durante la fusión del core.
        this._stockMergeSource = line;
        try {
            return super.merge(line);
        } finally {
            this._stockMergeSource = null;
        }
    },

    setQuantity(quantity, keepPrice = false) {
        const error = stockError(this.order_id, this.product_id, Number(quantity), this);
        if (error) {
            const notification = this.env?.services?.notification ||
                this.models?.env?.services?.notification || stockNotification;
            notification?.add(error, { type: "warning" });
            return false;
        }
        return super.setQuantity(quantity, keepPrice);
    },
});
