import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { useService } from "@web/core/utils/hooks";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { printableQuotations } from "../utils/quotation_print";

patch(PosOrder.prototype, {
    serializeForORM() {
        const values = super.serializeForORM(...arguments);
        if (this.reinvoice_revision) {
            values.reinvoice_revision = this.reinvoice_revision;
        }
        return values;
    },
});

patch(PaymentScreen.prototype, {
    get canCancelReinvoiceRefund() {
        const order = this.currentOrder;
        return order?.isRefund && Number.isInteger(order.id) && !order.finalized && !order.payment_ids.length;
    },
    async cancelReinvoiceRefund() {
        const order = this.currentOrder;
        if (!this.canCancelReinvoiceRefund || !await ask(this.dialog, {
            title: _t("Deshacer refacturación"),
            body: _t("Se descartará la devolución pendiente. La orden original conservará su estado actual."),
        })) {
            return;
        }
        try {
            await this.pos.data.call("pos.order", "cancel_reinvoice_refund", [order.uuid, this.pos.session.id]);
            const wasSelected = this.pos.getOrder() === order;
            this.pos.removeOrder(order, false);
            if (!this.pos.getOrder()) {
                this.pos.addNewOrder();
            }
            if (wasSelected) {
                this.pos.navigate("ProductScreen");
            }
        } catch (error) {
            this.notification.add(error.data?.message || error.message, { type: "warning" });
        }
    },
});

patch(PosStore.prototype, {
    async pay() {
        const order = this.getOrder();
        if (order?.isRefund && Number.isInteger(order.id) &&
            !order.lines.some((line) => line.refunded_orderline_id)) {
            try {
                await this.data.callRelated("pos.order", "read_refund_origin_for_payment", [order.uuid], {}, false, true);
                if (this.getOrder() !== order) {
                    return;
                }
                await this.data.callRelated("pos.order", "read_pos_data_uuid", [order.uuid], {}, false, true);
                if (this.getOrder() !== order) {
                    return;
                }
            } catch (error) {
                this.notification.add(error.data?.message || _t("No se pudo cargar la venta original del reembolso."), {
                    type: "warning",
                });
                return;
            }
        }
        return super.pay(...arguments);
    },

    async addLineToCurrentOrder(values, options = {}, configure = true) {
        const recipe = values.sale_order_line_id?.tint_snapshot;
        if (recipe?.base_only) {
            values = { ...values, is_tinted_base: true, tint_color_code: recipe.color_code || false,
                       customer_note: values.sale_order_line_id.name };
        }
        return super.addLineToCurrentOrder(values, options, configure);
    },
});

patch(ControlButtons.prototype, {
    setup() {
        super.setup(...arguments);
        this.quotationUI = useService("ui");
        this.quotationAction = useService("action");
    },

    get printableSaleQuotations() {
        return printableQuotations(this.pos.getOrder(), this.pos.lastCreatedSaleQuotation);
    },

    async onClickReinvoiceSaleOrder() {
        const model = await makeAwaitable(this.dialog, SelectionPopup, {
            title: _t("¿Dónde se registró la venta original?"),
            list: [
                { id: "pos", label: _t("Punto de Venta"), item: "pos.order" },
                { id: "sale", label: _t("Ventas"), item: "sale.order" },
            ],
        });
        if (!model) {
            return;
        }
        const isPos = model === "pos.order";
        const paid = [["move_type", "=", "out_invoice"], ["is_advance_invoice", "=", false],
            ["state", "=", "posted"], ["payment_state", "=", "paid"], ["amount_total", ">", 0]];
        const unpaid = [["state", "!=", "cancel"], "|", ["state", "!=", "posted"], ["payment_state", "!=", "paid"]];
        const saleInvoiceLines = (invoiceDomain) =>
            [["order_line", "any", [["invoice_lines", "any", [["move_id", "any", invoiceDomain]]]]]];
        const paidDomain = isPos
            ? [["account_move", "any", paid], ["split_invoice_ids", "=", false]]
            : [...saleInvoiceLines(paid), ["order_line", "not any",
                [["invoice_lines", "any", [["move_id", "any", [
                    ["move_type", "=", "out_invoice"], ["is_advance_invoice", "=", false], ...unpaid,
                ]]]]]]];
        this.dialog.add(SelectCreateDialog, {
            title: _t("Selecciona la orden para refacturar"),
            resModel: model,
            noCreate: true,
            multiSelect: false,
            domain: [
                ["company_id", "=", this.pos.company.id],
                ["state", "in", isPos ? ["paid", "done"] : ["sale", "cancel"]],
                ["currency_id", "=", this.pos.currency.id],
                ...(isPos ? [["is_refund", "=", false], ["session_id", "=", this.pos.session.id]] : []),
                ...paidDomain,
            ],
            onSelected: ([orderId]) => this.recoverReinvoiceTicket(model, orderId),
        });
    },

    async recoverReinvoiceTicket(model, orderId) {
        const isPos = model === "pos.order";
        const confirmed = await ask(this.dialog, {
            title: _t("Reabrir la orden original"),
            body: _t("Confirma que devolviste el efectivo al cliente. Odoo cancelará la factura y el cobro elegibles, devolverá las existencias y abrirá esta misma orden en borrador."),
        });
        if (!confirmed) {
            return;
        }
        this.quotationUI.block();
        try {
            const method = isPos ? "reopen_original_pos_for_reinvoice" : "reopen_original_sale_for_reinvoice";
            const result = await this.pos.data.call("pos.order", method, [
                orderId, this.pos.session.id, true,
            ]);
            if (isPos) {
                const orders = await this.pos.data.loadServerOrders([["uuid", "=", result]]);
                const ticket = orders.find((order) => order.uuid === result && order.state === "draft");
                if (!ticket) {
                    throw new Error(_t("La orden quedó reabierta. Recarga la caja para recuperarla."));
                }
                this.pos.setOrder(ticket);
                this.props.close?.();
                this.pos.navigate("ProductScreen");
                return;
            }
            this.props.close?.();
            this.dialog.add(FormViewDialog, {
                resModel: "sale.order", resId: result.sale_id, canExpand: false,
                title: _t("Editar la orden original"),
            });
        } catch (error) {
            this.notification.add(error.data?.message || error.message, { type: "warning" });
        } finally {
            this.quotationUI.unblock();
        }
    },

    async printSaleQuotation(quotation) {
        try {
            await this.quotationAction.doAction("sale.action_report_saleorder", {
                additionalContext: { active_model: "sale.order", active_ids: [quotation.id], active_id: quotation.id },
            });
        } catch (error) {
            this.notification.add(error.data?.message || _t("No se pudo imprimir la cotización. Puedes reintentar desde Ventas."),
                { type: "danger" });
        }
    },

    async onClickPrintSaleQuotation() {
        const options = this.printableSaleQuotations;
        let quotation = options[0];
        if (options.length > 1) {
            quotation = await makeAwaitable(this.dialog, SelectionPopup, {
                title: _t("Imprimir cotización"),
                list: options.map((item) => ({ id: item.id, label: item.name || item.display_name, item })),
            });
        }
        if (quotation) {
            await this.printSaleQuotation(quotation);
            this.props.close?.();
        }
    },

    async onClickCreateSaleQuotation() {
        if (this._creatingSaleQuotation) {
            return;
        }
        const order = this.pos.getOrder();
        if (!order?.getPartner() || !order.lines.length) {
            this.notification.add(_t("Selecciona un cliente y agrega los productos de la cotización."), { type: "warning" });
            return;
        }
        if (order.payment_ids.length || order.lines.some((line) => line.sale_order_origin_id || line.sale_order_line_id)) {
            this.notification.add(_t("Usa un ticket sin pagos y que no provenga de una cotización u orden de venta."), { type: "warning" });
            return;
        }
        const confirmed = await ask(this.dialog, {
            title: _t("Crear cotización"),
            body: _t("Se guardará este ticket como cotización en Ventas, sin cobrar ni entregar productos. Después podrás recuperarla desde Cotización/Orden. El ticket actual se retirará de la caja."),
        });
        if (!confirmed || this.pos.getOrder() !== order) {
            return;
        }
        this._creatingSaleQuotation = true;
        this.quotationUI.block();
        try {
            order.setOrderPrices();
            const expected = {
                partner_id: order.getPartner().id, pricelist_id: order.pricelist_id?.id || false,
                fiscal_position_id: order.fiscal_position_id?.id || false,
                lines: order.lines.map((line) => [line.uuid, line.product_id.id, line.qty, line.price_unit,
                    line.discount ?? 0, line.combo_parent_id?.uuid || false, line.customer_note || "", line.tint_color_code || false]),
            };
            // Create only a sales quotation. Its source UUID makes retries safe
            // without saving or cancelling an order in POS.
            const quotation = await this.pos.data.call("pos.order", "create_sale_quotation_from_ui",
                [order.serializeForORM({ keepCommands: true }), expected], {
                    context: this.pos.getSyncAllOrdersContext([order]),
                });
            this.pos.lastCreatedSaleQuotation = quotation;
            this.pos.removePendingOrder(order);
            this.pos.removeOrder(order, false);
            if (!this.pos.getOrder()) {
                this.pos.addNewOrder();
            }
            this.pos.navigate("ProductScreen");
            this.notification.add(_t("Cotización %s creada en Ventas. Puedes recuperarla desde Cotización/Orden.", quotation.name),
                                  { type: "success", sticky: true,
                                    buttons: [{ name: _t("Imprimir cotización"), onClick: () => this.printSaleQuotation(quotation) }] });
            this.props.close?.();
        } catch (error) {
            this.notification.add(error.data?.message || error.message || _t("No se pudo crear la cotización. El ticket se conserva para reintentar."),
                                  { type: "danger", sticky: true });
        } finally {
            this.quotationUI.unblock();
            this._creatingSaleQuotation = false;
        }
    },
});
