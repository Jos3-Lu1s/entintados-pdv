/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { _t } from "@web/core/l10n/translation";

patch(ControlButtons.prototype, {
    openTintColorScreen() {
        const tintCategory = this.pos.models["pos.category"]
            ?.getAll()
            .find((c) => c.name === "Carta de Colores" || c.is_tint_category);

        if (tintCategory && typeof this.pos.setSelectedCategory === "function") {
            this.pos.setSelectedCategory(tintCategory);
        } else {
            const order = this.pos.getOrder();
            if (order) {
                this.pos.navigate("TintColorScreen", { orderUuid: order.uuid });
            }
        }
    },
});