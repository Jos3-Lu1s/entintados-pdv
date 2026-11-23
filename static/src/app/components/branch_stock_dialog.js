import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { formatFloat } from "@web/views/fields/formatters";

export class BranchStockDialog extends Component {
    static template = "entintados_pdv.BranchStockDialog";
    static components = { Dialog };
    static props = { close: Function, rows: Array, title: String };

    formatQuantity(quantity) {
        return formatFloat(quantity, { digits: [16, 4] });
    }
}
