import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";

patch(ControlButtons.prototype, {
    async onClickAdvances(requestedAction = null) {
        const order = this.pos.getOrder();
        const partner = order?.getPartner();
        if (!partner) {
            this.notification.add(_t("Selecciona el cliente para consultar o cobrar anticipos."), { type: "warning" });
            return;
        }
        if (!this.pos.config.advance_risk_enabled) {
            this.notification.add(_t("Los anticipos no están configurados para esta caja. Un administrador contable debe preparar las cuentas y el producto genérico de anticipo en Ajustes."), { type: "warning" });
            return;
        }
        try {
            const info = await this.pos.data.call("res.partner", "get_advance_pos_info", [[partner.id], this.pos.config.id]);
            const format = (value) => this.pos.env.utils.formatCurrency(value);
            const action = requestedAction || await makeAwaitable(this.dialog, SelectionPopup, {
                title: _t("Saldo a favor del cliente: %s", format(info.advance_available)),
                list: [
                    { id: "collect", label: _t("Recibir dinero del cliente"), description: _t("El importe pagado quedará a su favor para próximas compras."), item: "collect" },
                    { id: "apply", label: _t("Usar saldo a favor en esta venta"), description: _t("Descuenta el anticipo recibido del importe que el cliente debe pagar."), item: "apply" },
                ],
            });
            if (!action) {
                return;
            }
            const product = this.pos.config.down_payment_product_id;
            const template = product?.product_tmpl_id;
            if (!template) {
                this.notification.add(_t("Selecciona el producto de anticipo en los ajustes del Punto de Venta, sección Ventas, y vuelve a cargar la caja."), { type: "warning" });
                return;
            }
            if (action === "collect") {
                const value = await makeAwaitable(this.dialog, NumberPopup, { title: _t("Importe del anticipo antes de impuestos"), startingValue: "0" });
                if (value === undefined) {
                    return;
                }
                const amount = Number(value);
                if (!Number.isFinite(amount) || amount <= 0) {
                    return;
                }
                // Preserve the sale and any payments already entered on its ticket.
                const advanceOrder = order.lines.length || order.payment_ids.length
                    ? this.pos.addNewOrder() : order;
                if (advanceOrder !== order) {
                    advanceOrder.setPartner(partner);
                }
                const line = await this.pos.addLineToCurrentOrder({ product_id: product, product_tmpl_id: template, price_unit: amount, qty: 1, price_type: "manual" }, { merge: false }, false);
                if (!line) {
                    return;
                }
                advanceOrder.setToInvoice(true);
                return;
            }
            const usable = info.sources.filter((source) => source.fiscal_kind !== "fiscal");
            if (!usable.length) {
                this.notification.add(_t("No hay anticipos disponibles para aplicar en caja. Los anticipos fiscales se aplican desde Contabilidad."), { type: "warning" });
                return;
            }
            if (!order.lines.length) {
                this.notification.add(_t("Agrega los productos de la venta antes de aplicar el anticipo."), { type: "warning" });
                return;
            }
            if (order.electronicPaymentInProgress?.()) {
                this.notification.add(_t("Finaliza o cancela el pago electrónico en curso antes de aplicar el anticipo."), { type: "warning" });
                return;
            }
            if (order.payment_ids.some((payment) => payment.amount < 0 ||
                (payment.payment_method_id?.type === "pay_later" && payment.amount > 0))) {
                this.notification.add(_t("Retira el saldo pendiente o la devolución del ticket antes de aplicar el anticipo."), { type: "warning" });
                return;
            }
            const source = await makeAwaitable(this.dialog, SelectionPopup, {
                title: _t("Selecciona el anticipo"),
                list: usable.map((item) => ({ id: item.id, label: `${item.name}: ${format(item.available)}`, item })),
            });
            if (!source) {
                return;
            }
            const cartReserved = -order.lines.filter((line) => line.advance_source_ref === source.id)
                .reduce((total, line) => total + line.prices.total_included_currency, 0);
            const paid = order.payment_ids.reduce((total, payment) => total + Number(payment.amount || 0), 0);
            const maximum = Math.min(source.available - cartReserved, order.priceIncl - paid);
            if (!(maximum > 0)) {
                this.notification.add(_t("No queda importe pendiente en esta venta o saldo disponible en el anticipo seleccionado."), { type: "warning" });
                return;
            }
            const value = await makeAwaitable(this.dialog, NumberPopup, { title: _t("Importe a aplicar con impuestos"), startingValue: String(maximum) });
            if (value === undefined) {
                return;
            }
            const amount = Number(value);
            const currentPaid = order.payment_ids.reduce((total, payment) => total + Number(payment.amount || 0), 0);
            if (this.pos.getOrder() !== order || order.getPartner()?.id !== partner.id) {
                this.notification.add(_t("La venta o el cliente cambió. Vuelve a seleccionar el anticipo."), { type: "warning" });
                return;
            }
            if (!Number.isFinite(amount) || amount <= 0 || amount > maximum || amount > order.priceIncl - currentPaid) {
                this.notification.add(_t("El importe supera el saldo disponible o el total de la venta."), { type: "warning" });
                return;
            }
            const prepared = source.lines.map((original) => {
                const taxes = original.tax_ids.map((id) => this.pos.models["account.tax"].get(id));
                if (taxes.some((tax) => !tax)) {
                    throw new Error(_t("Los impuestos del anticipo no están cargados en la caja."));
                }
                if (original.discount) {
                    throw new Error(_t("Este anticipo tiene descuentos: aplícalo desde Contabilidad."));
                }
                if (taxes.some((tax) => tax.amount_type === "fixed")) {
                    throw new Error(_t("Este anticipo tiene impuestos fijos: aplícalo desde Contabilidad."));
                }
                return { original, taxes };
            });
            for (const { original, taxes } of prepared) {
                const line = await this.pos.addLineToCurrentOrder({
                    product_id: product, product_tmpl_id: template, price_unit: -original.price_unit * amount / source.total,
                    qty: original.quantity, tax_ids: taxes.map((tax) => ["link", tax]), advance_source_ref: source.id,
                }, { merge: false }, false);
                line.advance_source_ref = source.id;
                line.price_type = "manual";
            }
            order.setToInvoice(true);
        } catch (error) {
            this.notification.add(error.message || _t("No se pudo consultar el anticipo. Se requiere conexión con el servidor."), { type: "danger" });
        }
    },
});
