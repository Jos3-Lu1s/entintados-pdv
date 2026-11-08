import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { useService } from "@web/core/utils/hooks";
import { printableQuotations } from "../utils/quotation_print";

patch(PosStore.prototype, {
    async addLineToCurrentOrder(values, options = {}, configure = true) {
        const recipe = values.sale_order_line_id?.tint_snapshot;
        if (recipe) {
            values = {
                ...values,
                is_tinted_base: true,
                tint_color_code: recipe.color_code || false,
                customer_note: values.sale_order_line_id.name,
                price_unit: recipe.unit_price || values.price_unit,
            };
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
