import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { runTintFlow } from "@entintados_pdv/app/utils/tint_flow";

patch(ControlButtons.prototype, {
    /** Entinta la línea base seleccionada en la orden. */
    async onClickTint() {
        const order = this.pos.getOrder();
        const line = order?.getSelectedOrderline();

        if (!line) {
            this.notification.add(
                _t("Selecciona primero una base de pintura en la orden para entintarla."),
                { type: "warning" }
            );
            return;
        }

        if (line.combo_line_ids?.length) {
            this.notification.add(
                _t("Esta línea ya está entintada. Elimínala y vuelve a agregarla para cambiar el color."),
                { type: "warning" }
            );
            return;
        }

        await runTintFlow(this, {
            baseProduct: line.product_id,
            replaceLine: line,
            qty: line.qty || 1,
            initialColorId: order.uiState?.selectedTintColorId || false,
        });
    },
});
