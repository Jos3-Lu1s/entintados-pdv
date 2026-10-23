import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosStore } from "@point_of_sale/app/services/pos_store";

patch(PosStore.prototype, {
    async refreshTotalDueOfPartner(partner) {
        const updates = await super.refreshTotalDueOfPartner(...arguments);
        for (const values of updates || []) {
            const local = this.models["res.partner"].get(values.id);
            if (local && values.customer_advance_available !== undefined) {
                local.customer_advance_available = values.customer_advance_available;
            }
        }
        return updates;
    },
    async pay() {
        const order = this.getOrder();
        if (this._applyingCustomerAdvance) {
            return;
        }
        if (!this.config.advance_risk_enabled || order?.uiState?.skipAutomaticCustomerAdvance ||
            !order?.getPartner() || order.priceIncl <= 0 ||
            order.payment_ids.length || order.isRefund || order.is_settling_account ||
            order.lines.some((line) => line.isSettleDueLine?.() || line.isSettleInvoiceLine?.() ||
                (line.price_unit * line.qty > 0 && (line.product_id?.product_tmpl_id?.is_accounting_advance ||
                    line.product_id?.id === this.config.down_payment_product_id?.id)))) {
            return await super.pay(...arguments);
        }
        this._applyingCustomerAdvance = true;
        try {
            const partner = order.getPartner();
            const info = await this.data.call("res.partner", "get_advance_pos_info", [[partner.id], this.config.id]);
            if (this.getOrder() !== order || order.getPartner() !== partner) {
                return;
            }
            // The cashier may remove an advance while the balance RPC is pending.
            if (order.uiState?.skipAutomaticCustomerAdvance) {
                return await super.pay(...arguments);
            }
            let remaining = order.priceIncl;
            const product = this.config.down_payment_product_id;
            const planned = [];
            for (const source of info.sources) {
                if (source.fiscal_kind === "fiscal" || remaining <= 0) {
                    continue;
                }
                const reserved = -order.lines.filter((line) => line.advance_source_ref === source.id)
                    .reduce((sum, line) => sum + line.prices.total_included_currency, 0);
                const amount = Math.min(Math.max(source.available - reserved, 0), remaining);
                if (amount <= 0) {
                    continue;
                }
                if (!product?.product_tmpl_id || !(source.total > 0)) {
                    throw new Error(_t("Revisa la configuración del producto de anticipos."));
                }
                for (const original of source.lines) {
                    const taxes = original.tax_ids.map((id) => this.models["account.tax"].get(id));
                    if (original.discount || taxes.some((tax) => !tax || tax.amount_type === "fixed")) {
                        throw new Error(_t("Este anticipo requiere aplicación desde Contabilidad por sus impuestos o descuentos."));
                    }
                    planned.push({ product_id: product, product_tmpl_id: product.product_tmpl_id,
                        price_unit: -original.price_unit * amount / source.total, qty: original.quantity,
                        price_type: "manual", tax_ids: taxes.map((tax) => ["link", tax]), advance_source_ref: source.id });
                }
                remaining -= amount;
            }
            for (const values of planned) {
                const line = await this.addLineToCurrentOrder(values, { merge: false }, false);
                if (!line) {
                    throw new Error(_t("No se pudo aplicar el saldo a favor. Revisa el ticket antes de pagar."));
                }
                line.advance_source_ref = values.advance_source_ref;
                line.price_type = "manual";
                order.setToInvoice(true);
            }
            return await super.pay(...arguments);
        } catch (error) {
            this.notification.add(error.message || _t("No se pudo consultar el saldo a favor. Revisa la conexión antes de cobrar."), { type: "danger" });
        } finally {
            this._applyingCustomerAdvance = false;
        }
    },
    async addDownPaymentProductOrderlineToOrder(saleOrder, amount, isPercentage) {
        if (!Number.isFinite(amount) || amount === 0 ||
            (amount > 0 && (isPercentage ? amount > 100 : amount > saleOrder.amount_unpaid))) {
            this.notification.add(_t("El anticipo debe ser positivo y no superar el saldo pendiente del pedido (máximo 100%)."), { type: "warning" });
            return;
        }
        // Negative amounts are also used by native settleSO to deduct prior payments.
        if (amount < 0 && isPercentage) {
            return;
        }
        return await super.addDownPaymentProductOrderlineToOrder(...arguments);
    },
    async addLineToCurrentOrder(values, options = {}, configure = true) {
        const line = await super.addLineToCurrentOrder(...arguments);
        if (line && this.config.advance_risk_enabled &&
            values.product_id?.id === this.config.down_payment_product_id?.id) {
            line.order_id.setToInvoice(true);
        }
        return line;
    },
});
