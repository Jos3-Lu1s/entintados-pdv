import { Component, useEffect, onMounted } from "@odoo/owl";
import { formatFloat } from "@web/views/fields/formatters";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { ProductInfoPopup } from "@point_of_sale/app/components/popups/product_info_popup/product_info_popup";
import { CustomerContactInfo } from "@entintados_pdv/app/components/customer_contact_info/customer_contact_info";
import { customerSummary } from "@entintados_pdv/app/utils/customer_summary";
import {
    canSplitInvoice,
    initializePaymentInvoice,
    isInvoiceCollection,
    setInvoiceSplitCount,
} from "@entintados_pdv/app/utils/invoice_options";

/**
 * Componente que muestra el resumen detallado de compra en la pantalla de pago:
 * datos de contacto, partidas, márgenes, opciones fiscales CFDI y desglose de totales.
 */
export class CustomerPaymentSummary extends Component {
    static template = "entintados_pdv.CustomerPaymentSummary";
    static components = { CustomerContactInfo };
    static props = {
        currentOrder: { type: Object, optional: true },
    };

    setup() {
        try {
            this.pos = usePos();
        } catch {
            this.pos = this.env.services?.pos || this.props.pos || {};
        }

        useEffect(() => {
            initializePaymentInvoice(this.currentOrder, this.pos?.company?.country_id?.code === "MX");
        }, () => [this.currentOrder?.uuid, this.currentOrder?.to_invoice]);

        onMounted(() => {
            if (this.currentOrder) {
                initializePaymentInvoice(this.currentOrder, this.pos?.company?.country_id?.code === "MX");
            }
        });
    }

    get currentOrder() {
        return this.props.currentOrder;
    }

    get showPaymentInvoiceOptions() {
        return !!this.currentOrder && this.pos?.company?.country_id?.code === "MX" &&
            !isInvoiceCollection(this.currentOrder);
    }

    get paymentInvoiceUsage() {
        return this.currentOrder?.l10n_mx_edi_usage || "";
    }

    get canSplitPaymentInvoice() {
        return canSplitInvoice(this.currentOrder);
    }

    get isAdvanceReceipt() {
        return Boolean(this.currentOrder?.lines?.some((line) =>
            !line.advance_source_ref && line.price_unit * line.qty > 0 &&
            (line.product_id?.product_tmpl_id?.is_accounting_advance ||
             line.product_id?.id === this.pos.config.down_payment_product_id?.id)
        ));
    }

    get paymentInvoiceSplitCount() {
        return this.canSplitPaymentInvoice
            ? this.currentOrder?.uiState?.invoice_split_count || this.currentOrder?.invoice_split_count || 1 : 1;
    }

    changePaymentInvoiceUsage(event) {
        if (this.currentOrder) {
            this.currentOrder.l10n_mx_edi_usage = event.target.value;
        }
    }

    changePaymentInvoicePublic(event) {
        if (this.currentOrder) {
            this.currentOrder.l10n_mx_edi_cfdi_to_public = event.target.value === "1";
        }
    }

    changePaymentInvoiceSplitCount(event) {
        if (this.currentOrder) {
            event.target.value = String(setInvoiceSplitCount(this.currentOrder, event.target.value));
        }
    }

    get showPaymentTerm() {
        return !!this.currentOrder?.payment_ids?.some((line) =>
            line.payment_method_id?.type === "pay_later" && line.amount > 0);
    }

    get paymentTermOptions() {
        return this.pos?.models?.["account.payment.term"]?.getAll?.() || [];
    }

    get defaultPaymentTerm() {
        const partnerTerm = this.currentOrder?.getPartner?.()?.property_payment_term_id;
        const configTerm = this.pos?.config?.ppd_payment_term_id;
        const options = this.paymentTermOptions;
        return options.find((term) => term.id === partnerTerm?.id) ||
            options.find((term) => term.id === configTerm?.id);
    }

    changePaymentTerm(event) {
        const term = this.pos?.models?.["account.payment.term"]?.get?.(Number(event.target.value));
        this.currentOrder?.update?.({ pos_payment_term_id: term || false });
    }

    formatMarginPercent(percent) {
        return formatFloat(percent, { digits: [16, 2] });
    }

    get customerPaymentSummary() {
        const showMargins = ProductInfoPopup.prototype._hasMarginsCostsAccessRights?.call(this) || false;
        return customerSummary(this.currentOrder, showMargins);
    }
}
