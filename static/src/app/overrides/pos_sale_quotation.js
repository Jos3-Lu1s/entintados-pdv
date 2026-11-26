import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { useService } from "@web/core/utils/hooks";
import { downloadFile } from "@web/core/network/download";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { printableQuotations } from "../utils/quotation_print";
import { BranchStockDialog } from "../components/branch_stock_dialog";
import { requiresPosStock } from "../utils/pos_stock";
import { isTintColorant } from "../utils/tint_order";

async function selectQuotation(dialog, options, title) {
    if (options.length > 1) {
        return makeAwaitable(dialog, SelectionPopup, {
            title,
            list: options.map((item) => ({ id: item.id, label: item.name || item.display_name, item })),
        });
    }
    return options[0];
}

function downloadSaleQuotation(action, quotation) {
    return action.doAction("sale.action_report_saleorder", {
        additionalContext: { active_model: "sale.order", active_ids: [quotation.id], active_id: quotation.id },
    });
}

patch(TicketScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.quotationAction = useService("action");
    },

    hasLinkedQuotation(order) {
        return printableQuotations(order).length > 0;
    },

    async onClickDownloadQuotation(order) {
        try {
            const quotation = await selectQuotation(
                this.dialog, printableQuotations(order), _t("Descargar cotización")
            );
            if (quotation) {
                await downloadSaleQuotation(this.quotationAction, quotation);
            }
        } catch (error) {
            this.pos.notification.add(error.data?.message || _t("No se pudo descargar la cotización."), {
                type: "danger",
            });
        }
    },
});

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

    get canCheckQuotationStock() {
        const order = this.pos.getOrder();
        return order?.state === "draft" && !order.finalized && !order.isRefund &&
            order.lines.some((line) => line.qty > 0 && requiresPosStock(line.product_id));
    },

    onClickQuotationStock() {
        if (!this.canCheckQuotationStock) {
            return;
        }
        const order = this.pos.getOrder();
        const lines = [...new Set(order.lines.flatMap((line) => [line, ...(line.combo_line_ids || [])]))];
        const tintChildren = new Set(lines.flatMap((line) => (line.combo_line_ids || []).filter(isTintColorant)));
        const entries = lines.flatMap((line, index) => {
            const product = line.product_id;
            if (line.qty <= 0 || tintChildren.has(line) || !requiresPosStock(product)) {
                return [];
            }
            const quantities = {};
            const tintPoints = {};
            const recipe = line.sale_order_line_id?.tint_snapshot;
            if (isTintColorant(line)) {
                tintPoints[product.id] = Number(line.qty);
            } else {
                quantities[product.id] = Number(line.qty);
                if (recipe?.base_id === product.id && recipe.doses?.length) {
                    for (const dose of recipe.doses) {
                        tintPoints[dose.product_id] = (tintPoints[dose.product_id] || 0) + dose.points * Number(line.qty);
                    }
                } else {
                    for (const child of (line.combo_line_ids || []).filter(isTintColorant)) {
                        if (child.qty > 0 && child.product_id?.id) {
                            const id = child.product_id.id;
                            tintPoints[id] = (tintPoints[id] || 0) + Number(child.qty);
                        }
                    }
                }
            }
            const name = line.getFullProductName?.() || product.display_name || product.name || "";
            return [{
                label: _t("%s · Línea %s", name, index + 1),
                productId: product.id,
                quantity: Number(line.qty),
                unit: product.uom_id?.name || "",
                reference: (line.sale_order_origin_id || line.sale_order_line_id?.order_id)?.name ||
                    order.getName?.() || order.pos_reference || "",
                quantities,
                tintPoints,
            }];
        });
        const config = this.pos.config;
        this.dialog.add(BranchStockDialog, {
            entries,
            title: _t("Disponibilidad por sucursal"),
            company: this.pos.company?.name || "",
            loadStock: (entry) => this.pos.data.call("product.product", "get_pos_quotation_branch_stock",
                [config.id, entry.quantities, entry.tintPoints]),
        });
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
        const invoiceDomain = [["move_type", "=", "out_invoice"], ["is_advance_invoice", "=", false],
            "|", ["state", "=", "cancel"], ["payment_state", "=", "paid"]];
        const invoiced = isPos
            ? [["account_move", "any", invoiceDomain], ["split_invoice_ids", "=", false]]
            : [["order_line", "any", [["invoice_lines", "any", [["move_id", "any", invoiceDomain]]]]]];
        this.dialog.add(SelectCreateDialog, {
            title: _t("Selecciona la orden para restablecer"),
            resModel: model,
            noCreate: true,
            multiSelect: false,
            domain: [
                ["company_id", "=", this.pos.company.id],
                ["state", "in", isPos ? ["paid", "done", "cancel"] : ["sale", "cancel"]],
                ["currency_id", "=", this.pos.currency.id],
                ...(isPos ? [["is_refund", "=", false], ["session_id", "=", this.pos.session.id]] : []),
                ...invoiced,
            ],
            onSelected: ([orderId]) => this.recoverReinvoiceTicket(model, orderId),
        });
    },

    async recoverReinvoiceTicket(model, orderId) {
        const confirmed = await ask(this.dialog, {
            title: _t("Restablecer orden"),
            body: _t("Confirma que devolviste el cobro al cliente. Odoo revertirá las operaciones elegibles y abrirá un ticket nuevo vinculado al original. Si falta confirmar la cancelación ante el SAT, se abrirá la factura anterior para revisarla."),
        });
        if (!confirmed) {
            return;
        }
        this.quotationUI.block();
        try {
            const result = await this.pos.data.call("pos.order", "restore_order", [
                model, orderId, this.pos.session.id, true,
            ]);
            if (result.invoice_id) {
                this.props.close?.();
                this.dialog.add(FormViewDialog, {
                    resModel: "account.move", resId: result.invoice_id, canExpand: false,
                    title: _t("Confirma la cancelación ante el SAT y vuelve a restablecer la orden"),
                });
                return;
            }
            const orders = await this.pos.data.loadServerOrders([["uuid", "=", result.uuid]]);
            const ticket = orders.find((order) => order.uuid === result.uuid && order.state === "draft");
            if (!ticket) {
                throw new Error(_t("Recarga la caja para recuperar el nuevo ticket."));
            }
            this.pos.setOrder(ticket);
            this.props.close?.();
            this.pos.navigate("ProductScreen");
        } catch (error) {
            this.notification.add(error.data?.message || error.message, { type: "warning" });
        } finally {
            this.quotationUI.unblock();
        }
    },

    async printSaleQuotation(quotation) {
        try {
            await downloadSaleQuotation(this.quotationAction, quotation);
        } catch (error) {
            this.notification.add(error.data?.message || _t("No se pudo imprimir la cotización. Puedes reintentar desde Ventas."),
                { type: "danger" });
        }
    },

    get canPrintCurrentQuotation() {
        const order = this.pos.getOrder();
        return order?.state === "draft" && !order.finalized && !order.isRefund && order.lines.length > 0 &&
            !printableQuotations(order).length;
    },

    async onClickPrintCurrentQuotation() {
        if (!this.canPrintCurrentQuotation || this._printingCurrentQuotation) {
            return;
        }
        const order = this.pos.getOrder();
        if (!order.getPartner()) {
            this.notification.add(_t("Selecciona un cliente para imprimir la cotización."), { type: "warning" });
            return;
        }
        this._printingCurrentQuotation = true;
        this.quotationUI.block();
        try {
            order.setOrderPrices();
            const snapshot = order.serializeForORM({ keepCommands: true });
            snapshot.tracking_number = order.tracking_number;
            // Synchronization sends only changed lines. The PDF needs the full order.
            snapshot.lines = order.lines.map((line) => [0, 0, {
                ...line.serializeForORM({ keepCommands: true }),
                combo_parent_id: line.combo_parent_id?.uuid || false,
            }]);
            const pdf = await this.pos.data.call("pos.order", "print_sale_quotation_from_ui", [
                snapshot,
            ], { context: this.pos.getSyncAllOrdersContext([order]) });
            await downloadFile(`data:application/pdf;base64,${pdf}`, "Cotizacion.pdf", "application/pdf");
            this.props.close?.();
        } catch (error) {
            this.notification.add(error.data?.message || _t("No se pudo imprimir la cotización de la orden."), {
                type: "warning",
            });
        } finally {
            this._printingCurrentQuotation = false;
            this.quotationUI.unblock();
        }
    },

    async onClickPrintSaleQuotation() {
        const quotation = await selectQuotation(this.dialog, this.printableSaleQuotations, _t("Imprimir cotización"));
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
