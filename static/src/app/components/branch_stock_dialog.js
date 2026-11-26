import { Component, useState } from "@odoo/owl";
import { AutoComplete } from "@web/core/autocomplete/autocomplete";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { formatFloat } from "@web/views/fields/formatters";

function searchable(text) {
    return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

export class BranchStockDialog extends Component {
    static template = "entintados_pdv.BranchStockDialog";
    static components = { AutoComplete, Dialog };
    static props = { close: Function, entries: Array, title: String, company: String, loadStock: Function };

    setup() {
        this.state = useState({ entry: null, rows: [], pending: false, error: "" });
        this.request = 0;
        this.sources = [{ options: (query) => {
            const search = searchable(query.trim());
            return this.props.entries
                .filter((entry) => searchable(entry.label).includes(search))
                .map((entry) => ({
                    label: entry.label,
                    onSelect: () => this.load(entry),
                }));
        } }];
    }

    async load(entry = this.state.entry) {
        if (!entry) {
            return;
        }
        const request = ++this.request;
        Object.assign(this.state, { entry, rows: [], pending: true, error: "" });
        try {
            const rows = await this.props.loadStock(entry);
            if (request === this.request) {
                this.state.rows = rows;
            }
        } catch (error) {
            if (request === this.request) {
                this.state.error = error.data?.message || _t("No se pudo consultar la disponibilidad. Revisa la conexión.");
            }
        } finally {
            if (request === this.request) {
                this.state.pending = false;
            }
        }
    }

    get orderWarehouse() {
        return this.state.rows.find((row) => row.isOrderWarehouse)?.warehouse || "";
    }

    formatQuantity(quantity) {
        return formatFloat(quantity, { digits: [16, 4] });
    }
}
