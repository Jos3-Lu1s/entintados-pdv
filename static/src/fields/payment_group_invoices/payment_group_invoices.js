import { useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";
import { ListRenderer } from "@web/views/list/list_renderer";

function normalize(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

export function matchingInvoices(records, query) {
    const terms = normalize(query).trim().split(/\s+/).filter(Boolean);
    return records.filter(({ data }) => {
        const text = normalize([
            data.invoice_id?.display_name,
            data.partner_id?.display_name,
            data.invoice_reference,
        ].join(" "));
        return terms.every((term) => text.includes(term));
    });
}

class PaymentInvoiceListRenderer extends ListRenderer {
    static rowsTemplate = "entintados_pdv.PaymentInvoiceRows";
    static props = [...ListRenderer.props, "invoiceQuery"];

    getMatchingInvoices(list) {
        return matchingInvoices(list.records, this.props.invoiceQuery);
    }
}

export class PaymentGroupInvoices extends X2ManyField {
    static template = "entintados_pdv.PaymentGroupInvoices";
    static components = { ...X2ManyField.components, ListRenderer: PaymentInvoiceListRenderer };

    setup() {
        super.setup();
        this.search = useState({ query: "", busy: false });
        onWillStart(() => this.loadAllInvoices());
    }

    async loadAllInvoices() {
        if (this.list.offset || this.list.records.length < this.list.count) {
            await this.list.load({ offset: 0, limit: Math.max(this.list.count, this.list.limit) });
        }
    }

    get rendererProps() {
        return { ...super.rendererProps, invoiceQuery: this.search.query };
    }

    async onInvoiceSearch(event) {
        this.search.query = event.target.value;
        await this.loadAllInvoices();
    }

    onSearchKeydown(event) {
        if (event.key === "Enter") {
            event.preventDefault();
            event.stopPropagation();
        }
    }

    async selectMatches(selected) {
        this.search.busy = true;
        try {
            await this.loadAllInvoices();
            for (const record of matchingInvoices(this.list.records, this.search.query)) {
                if (record.data.selected !== selected) {
                    await record.update({ selected });
                }
            }
        } finally {
            this.search.busy = false;
        }
    }
}

registry.category("fields").add("payment_group_invoices", {
    ...x2ManyField,
    component: PaymentGroupInvoices,
});
