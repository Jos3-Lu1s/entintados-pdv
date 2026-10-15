import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { CustomSelectCreateDialog } from "@pos_settle_due/app/views/view_dialogs/select_create_dialog";

patch(PaymentScreen.prototype, {
    clearDeferredPayment() {
        for (const line of [...this.currentOrder.payment_ids]) {
            if (line.payment_method_id.type === "pay_later") {
                line.delete();
            }
        }
        this.currentOrder.update({ pos_payment_flow: "standard" });
    },
    async setDeferredPayment(flow) {
        const order = this.currentOrder;
        const partner = order.getPartner();
        if (!partner || !order.lines.length || order.priceIncl <= 0 || order.is_settling_account ||
            order.lines.some((line) => line.isSettleDueLine?.() || line.isSettleInvoiceLine?.())) {
            this.notification.add(_t("Selecciona un cliente y agrega los productos de una venta. Esta opción no se usa para cobrar una deuda anterior."), { type: "warning" });
            return;
        }
        const method = this.pos.config.payment_method_ids.find((value) => value.type === "pay_later");
        if (!method) {
            this.notification.add(_t("Prepara el método de saldo pendiente en la configuración de esta caja."), { type: "warning" });
            return;
        }
        if (order.lines.some((line) => line.price_unit * line.qty > 0 &&
            (line.product_id?.product_tmpl_id?.is_accounting_advance ||
             line.product_id?.id === this.pos.config.down_payment_product_id?.id))) {
            this.notification.add(_t("Un anticipo que genera saldo a favor debe cobrarse completo con un medio real de pago."), { type: "warning" });
            return;
        }
        const real = order.payment_ids.filter((line) => line.payment_method_id.type !== "pay_later");
        const received = real.reduce((total, line) => total + line.amount, 0);
        const pending = this.pos.currency.round(order.priceIncl - received);
        if (real.some((line) => line.amount < 0) || pending <= 0 ||
            (flow === "ppd_partial" && received <= 0)) {
            this.notification.add(_t("Registra primero el importe que el cliente paga hoy y deja una parte pendiente. Para financiar toda la venta utiliza Crédito autorizado."), { type: "warning" });
            return;
        }
        try {
            if (flow === "credit") {
                const info = await this.pos.data.call("res.partner", "get_advance_pos_info", [[partner.id], this.pos.config.id]);
                const applied = -order.lines.filter((line) => line.advance_source_ref ||
                    (line.sale_order_line_id?.is_downpayment && line.price_unit * line.qty < 0))
                    .reduce((total, line) => total + line.prices.total_included_currency, 0);
                if (!info.credit_authorized || !info.credit_limited || info.risk_exception || pending > info.purchase_power - applied) {
                    this.notification.add(_t("El cliente no tiene crédito autorizado suficiente. Revisa su límite o registra un pago."), { type: "warning" });
                    return;
                }
            }
            const existing = order.payment_ids.filter((line) => line.payment_method_id.type === "pay_later");
            let line = existing[0];
            if (!line) {
                const result = order.addPaymentline(method);
                if (!result.status) {
                    this.notification.add(result.data, { type: "warning" });
                    return;
                }
                line = result.data;
            }
            for (const duplicate of existing.slice(1)) {
                duplicate.delete();
            }
            line.setAmount(pending);
            order.update({ pos_payment_flow: flow });
            order.setToInvoice(true);
        } catch (error) {
            this.notification.add(error.message || _t("No se pudo consultar el crédito. Revisa la conexión."), { type: "danger" });
        }
    },
    get deferredPaymentSummary() {
        const order = this.currentOrder;
        const pending = order.payment_ids.filter((line) => line.payment_method_id.type === "pay_later")
            .reduce((total, line) => total + line.amount, 0);
        return _t("Factura completa PPD · Saldo pendiente: %s", this.env.utils.formatCurrency(pending));
    },
});

patch(ControlButtons.prototype, {
    onClickCollectOutstandingInvoice() {
        const order = this.pos.getOrder();
        const partner = order?.getPartner();
        if (!partner || order.lines.length || order.payment_ids.length) {
            this.notification.add(_t("Selecciona el cliente en un ticket vacío para cobrar sus facturas pendientes."), { type: "warning" });
            return;
        }
        const view = this.pos.models["ir.ui.view"].find((value) => value.name === "due_account_move_list_view");
        if (!view) {
            this.notification.add(_t("Tu usuario necesita permiso de consulta de facturas para cobrarlas desde el POS."), { type: "warning" });
            return;
        }
        const commercialId = partner.commercial_partner_id?.id || partner.raw?.commercial_partner_id || partner.id;
        this.dialog.add(CustomSelectCreateDialog, {
            resModel: "account.move", noCreate: true, multiSelect: true, listViewId: view?.id,
            domain: [["commercial_partner_id", "=", commercialId], ["company_id", "=", this.pos.company.id],
                ["currency_id", "=", this.pos.currency.id], ["state", "=", "posted"],
                ["move_type", "=", "out_invoice"], ["pos_amount_unsettled", ">", 0]],
            onSelected: async (ids) => {
                await this.pos.onClickSettleInvoices(ids, partner.id, commercialId);
            },
        });
    },
});
