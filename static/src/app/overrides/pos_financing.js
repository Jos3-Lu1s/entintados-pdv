import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { CustomSelectCreateDialog } from "@pos_settle_due/app/views/view_dialogs/select_create_dialog";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { useEffect } from "@odoo/owl";

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        useEffect(() => {
            this.restoreCreditApproval();
        }, () => [this.currentOrder?.id, this.currentOrder?.getPartner()?.id]);
        useEffect(() => {
            if (!this.creditApprovalAmountsChanged || this.requestedCreditAmount <= 0) {
                return;
            }
            const order = this.currentOrder;
            const timer = setTimeout(() => {
                if (this.currentOrder === order) {
                    return this.setDeferredPayment('credit');
                }
            }, 500);
            return () => clearTimeout(timer);
        }, () => [this.currentOrder?.priceIncl, this.requestedCreditAmount, this.appliedCustomerAdvance,
            this.currentOrder?.uiState.creditApproval?.credit_amount,
            this.currentOrder?.uiState.creditApproval?.total_amount]);
        useEffect(() => {
            const order = this.currentOrder;
            if (!Number.isInteger(order?.id) || order.finalized || order.state === 'cancel' ||
                !['pending', 'approved'].includes(order.uiState.creditApproval?.state)) {
                return;
            }
            let busy = false;
            const timer = setInterval(async () => {
                if (busy || this.currentOrder !== order) {
                    return;
                }
                busy = true;
                try {
                    await this.restoreCreditApproval();
                } finally {
                    busy = false;
                }
            }, 5000);
            return () => clearInterval(timer);
        }, () => [this.currentOrder?.id, this.currentOrder?.state,
            this.currentOrder?.uiState.creditApproval?.id, this.currentOrder?.uiState.creditApproval?.state]);
    },
    get requestedCreditAmount() {
        const order = this.currentOrder;
        const received = order.payment_ids.filter((line) => line.payment_method_id.type !== 'pay_later')
            .reduce((total, line) => total + line.amount, 0);
        return Math.max(this.pos.currency.round(order.priceIncl - received), 0);
    },
    get creditApprovalAmountsChanged() {
        const order = this.currentOrder;
        const request = order.uiState.creditApproval;
        return Boolean(request && ['pending', 'approved'].includes(request.state) &&
            typeof request.credit_amount === 'number' && (
                this.pos.currency.round(this.requestedCreditAmount - request.credit_amount) !== 0 ||
                (typeof request.total_amount === 'number' && this.pos.currency.round(order.priceIncl - request.total_amount) !== 0) ||
                (typeof request.applied_amount === 'number' && this.pos.currency.round(this.appliedCustomerAdvance - request.applied_amount) !== 0)));
    },
    async restoreCreditApproval() {
        const order = this.currentOrder;
        if (!Number.isInteger(order?.id)) {
            return;
        }
        const partnerId = order.getPartner()?.id;
        const previousApproval = order.uiState.creditApproval;
        try {
            const request = await this.pos.data.call("pos.config", "get_order_credit_approval", [
                [this.pos.config.id], order.id,
            ]);
            if (this.currentOrder === order && order.getPartner()?.id === partnerId &&
                order.uiState.creditApproval === previousApproval) {
                order.uiState.creditApproval = request || undefined;
                if (request?.state === 'refused') {
                    this.clearDeferredPayment();
                    if (previousApproval && (previousApproval.id !== request.id || previousApproval.state !== 'refused')) {
                        await this.clearRejectedPayments();
                    }
                } else if (['void', 'cancelled'].includes(request?.state)) {
                    this.clearDeferredPayment();
                } else if (request?.state === 'approved') {
                    await this.applyApprovedCredit();
                }
            }
        } catch {
            // Credit validation always checks the live server state again before payment.
            // Keep the last known state so a temporary disconnect does not stop subsequent checks.
        }
    },
    async applyApprovedCredit() {
        const order = this.currentOrder;
        const request = order.uiState.creditApproval;
        if (this._applyingApprovedCredit || request?.state !== 'approved' ||
            typeof request.credit_amount !== 'number' || this.creditApprovalAmountsChanged ||
            this.requestedCreditAmount <= 0 || order.finalized || order.state === 'cancel') {
            return false;
        }
        const deferred = order.payment_ids.filter((line) => line.payment_method_id.type === 'pay_later');
        if (deferred.length === 1 && order.pos_payment_flow === 'credit' && order.to_invoice &&
            this.pos.currency.round(deferred[0].amount - this.requestedCreditAmount) === 0) {
            return true;
        }
        this._applyingApprovedCredit = true;
        try {
            return await this.setDeferredPayment('credit');
        } finally {
            this._applyingApprovedCredit = false;
        }
    },
    get isCreditCustomer() {
        const partner = this.currentOrder.getPartner();
        return Boolean(partner?.commercial_partner_id?.is_credit ?? partner?.is_credit);
    },
    get appliedCustomerAdvance() {
        return -this.currentOrder.lines.filter((line) => line.advance_source_ref ||
            (line.sale_order_line_id?.is_downpayment && line.price_unit * line.qty < 0))
            .reduce((sum, line) => sum + line.prices.total_included_currency, 0);
    },
    get isCollectingCustomerAdvance() {
        return this.currentOrder.lines.some((line) => line.price_unit * line.qty > 0 &&
            (line.product_id?.product_tmpl_id?.is_accounting_advance ||
                (this.pos.config.advance_risk_enabled &&
                    line.product_id?.id === this.pos.config.down_payment_product_id?.id)));
    },
    async addNewPaymentLine(paymentMethod) {
        if (paymentMethod.type === "pay_later" && !this.isCreditCustomer && !this.currentOrder.is_settling_account) {
            this.notification.add(_t("Las opciones de pago diferido requieren un contacto con la condición Crédito habilitada."), { type: "warning" });
            return false;
        }
        if (this.isCollectingCustomerAdvance && paymentMethod.type === "pay_later") {
            this.notification.add(_t("Recibe el anticipo con efectivo o tarjeta. Cuenta de cliente generaría una deuda, no un saldo a favor."), { type: "warning" });
            return false;
        }
        const order = this.currentOrder;
        if (paymentMethod.type === "pay_later" && !order.is_settling_account && !order.isRefund &&
            !order.lines.some((line) => line.isSettleDueLine?.() || line.isSettleInvoiceLine?.())) {
            if (this.canOfferDeferredPayment && this.pos.company?.country_id?.code === "MX") {
                if (this.isMxEdiPopupOpen) {
                    return false;
                }
                const partnerId = order.getPartner()?.id;
                this.isMxEdiPopupOpen = true;
                order.uiState.creditInvoiceSetup = true;
                try {
                    const confirmed = await this.pos.addL10nMxEdiFields(order);
                    if (!confirmed || this.currentOrder !== order || order.getPartner()?.id !== partnerId) {
                        return false;
                    }
                } finally {
                    this.isMxEdiPopupOpen = false;
                    delete order.uiState.creditInvoiceSetup;
                }
            }
            return (await this.setDeferredPayment("credit", true)) === true;
        }
        return await super.addNewPaymentLine(...arguments);
    },
    get requiresAdvanceInvoice() {
        return this.currentOrder.lines.some((line) =>
            line.product_id?.product_tmpl_id?.is_accounting_advance || line.advance_source_ref ||
            (this.pos.config.advance_risk_enabled &&
                line.product_id?.id === this.pos.config.down_payment_product_id?.id));
    },
    onMounted() {
        if (this.isCollectingCustomerAdvance || !this.isCreditCustomer) {
            this.payment_methods_from_config = this.payment_methods_from_config.filter((method) => method.type !== "pay_later");
        }
        super.onMounted(...arguments);
        if (this.requiresAdvanceInvoice) {
            this.currentOrder.setToInvoice(true);
        }
    },
    async toggleIsToInvoice() {
        if (this.requiresAdvanceInvoice) {
            this.currentOrder.setToInvoice(true);
            this.notification.add(_t("Los anticipos y sus aplicaciones requieren factura. Se generará al validar el pago."), { type: "info" });
            return;
        }
        return await super.toggleIsToInvoice(...arguments);
    },
    async validateOrder() {
        if (this.creditApprovalAmountsChanged && this.requestedCreditAmount > 0) {
            this.notification.add(_t('El monto cambió. Actualiza la autorización antes de validar la venta a crédito.'), { type: 'warning' });
            return false;
        }
        if (!this.isCreditCustomer && this.currentOrder.payment_ids.some(
            (line) => line.amount > 0 && line.payment_method_id.type === "pay_later")) {
            this.notification.add(_t("El cliente debe tener la condición Crédito habilitada para dejar saldo pendiente."), { type: "warning" });
            return false;
        }
        // Also repair existing draft tickets created before invoice enforcement.
        if (this.requiresAdvanceInvoice) {
            this.currentOrder.setToInvoice(true);
        }
        return await super.validateOrder(...arguments);
    },
    get canOfferDeferredPayment() {
        const order = this.currentOrder;
        return this.isCreditCustomer && !order.isRefund && !order.is_settling_account && order.priceIncl > 0 &&
            !order.lines.some((line) => line.isSettleDueLine?.() || line.isSettleInvoiceLine?.() ||
                (line.price_unit * line.qty > 0 &&
                    (line.product_id?.product_tmpl_id?.is_accounting_advance ||
                     line.product_id?.id === this.pos.config.down_payment_product_id?.id)));
    },
    async selectDeferredPayment() {
        const flow = await makeAwaitable(this.dialog, SelectionPopup, {
            title: _t("Forma de pago de la venta"),
            list: [
                { id: "standard", label: _t("Pago completo"), item: "standard" },
                { id: "credit", label: _t("Crédito autorizado"), item: "credit" },
            ],
        });
        if (flow === "standard") {
            this.clearDeferredPayment();
        } else if (flow) {
            await this.setDeferredPayment(flow);
        }
    },
    clearDeferredPayment() {
        for (const line of [...this.currentOrder.payment_ids]) {
            if (line.payment_method_id.type === "pay_later") {
                line.delete();
            }
        }
        this.currentOrder.update({ pos_payment_flow: "standard" });
    },
    async clearRejectedPayments() {
        const order = this.currentOrder;
        if (order.uiState.creditApproval?.state !== 'refused' || order.finalized ||
            (order.state && order.state !== 'draft')) {
            return false;
        }
        const terminalInProgress = order.payment_ids.some((line) => {
            const status = line.getPaymentStatus?.() || line.payment_status;
            return status && !['pending', 'retry'].includes(status);
        });
        if (terminalInProgress) {
            this.notification.add(_t('Cancela primero el cobro de terminal en curso o procesado antes de limpiar los pagos.'), { type: 'warning' });
            return false;
        }
        for (const line of [...order.payment_ids]) {
            line.delete();
        }
        order.uiState.selected_paymentline_uuid = undefined;
        order.update({ pos_payment_flow: 'standard' });
        try {
            if (Number.isInteger(order.id)) {
                await this.pos.syncAllOrders({ orders: [order], force: true, throw: true });
            }
            this.notification.add(_t('Pagos limpiados. Selecciona otra forma de pago. Un nuevo intento de crédito generará otro folio.'), { type: 'info' });
            return true;
        } catch (error) {
            this.notification.add(error.message || _t('Los pagos se limpiaron localmente; falta sincronizar la orden.'), { type: 'danger' });
            return false;
        }
    },
    async setDeferredPayment(flow, newAttempt = false) {
        const order = this.currentOrder;
        const partner = order.getPartner();
        if (!this.isCreditCustomer) {
            this.notification.add(_t("Las opciones de pago diferido requieren un contacto con la condición Crédito habilitada."), { type: "warning" });
            return;
        }
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
            const info = await this.pos.data.call("res.partner", "get_advance_pos_info", [[partner.id], this.pos.config.id]);
            if (!info.credit_authorized) {
                this.notification.add(_t("El contacto ya no tiene la condición Crédito habilitada."), { type: "warning" });
                return;
            }
            // Credit/PPD always produces one invoice, including tickets previously set to split.
            order.uiState.invoice_split_count = 1;
            order.invoice_split_count = 1;
            const applied = this.appliedCustomerAdvance;
            if (!info.credit_limited || info.risk_exception || pending > info.purchase_power ||
                ['pending', 'approved', 'refused'].includes(order.uiState.creditApproval?.state)) {
                order.setToInvoice(true);
                await this.pos.syncAllOrders({ orders: [order], force: true, throw: true });
                if (!Number.isInteger(order.id)) {
                    throw new Error(_t("No se pudo guardar el ticket en borrador. Revisa la conexión y vuelve a solicitar crédito."));
                }
                if (this.currentOrder !== order || order.getPartner()?.id !== partner.id) {
                    return false;
                }
                const request = await this.pos.data.call("pos.config", "request_credit_approval", [
                    [this.pos.config.id], partner.id, order.uuid, order.priceIncl, pending, applied, order.id, newAttempt,
                ]);
                order.uiState.creditApproval = request;
                if (request.state !== "approved") {
                    for (const line of [...order.payment_ids]) {
                        if (line.payment_method_id.type === 'pay_later') {
                            line.delete();
                        }
                    }
                    order.update({ pos_payment_flow: 'standard' });
                    if (request.state === 'refused') {
                        await this.clearRejectedPayments();
                    }
                    this.notification.add(request.state === "refused"
                        ? _t("La autorización de crédito fue rechazada. Registra un pago o revisa la venta.")
                        : _t("Autorización solicitada a Administración, Créditos y Tesorería. Cuando la aprueben, pulsa Revisar autorización."),
                    { type: "warning" });
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
            if (Number.isInteger(order.id) && order.uiState.creditApproval?.state === "approved") {
                await this.pos.syncAllOrders({ orders: [order], force: true, throw: true });
            }
            return true;
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
