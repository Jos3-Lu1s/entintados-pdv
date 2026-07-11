/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { makeAwaitable } from "@point_of_sale/app/store/make_awaitable_dialog";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { TintFormulaPopup } from "@entintados_pdv/js/tint_formula_popup";

patch(ControlButtons.prototype, {
    async onClickTint() {
        const order = this.pos.get_order();
        const line = order?.get_selected_orderline();

        if (!line) {
            this.notification.add(
                _t("Selecciona primero un producto en la orden para asignarle el entintado."),
                { type: "warning" }
            );
            return;
        }

        const payload = await makeAwaitable(this.dialog, TintFormulaPopup, { base: "" });
        if (!payload) {
            return;
        }

        line.setNote(payload.text);

        this.notification.add(_t("Entintado guardado en la línea."), { type: "success" });
    },
});
