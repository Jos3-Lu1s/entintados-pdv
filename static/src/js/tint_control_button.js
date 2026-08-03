import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { TintFormulaPopup } from "@entintados_pdv/js/tint_formula_popup";

patch(ControlButtons.prototype, {
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

        // La base y la presentación las determina el producto de la línea.
        const tmpl = line.product_id?.product_tmpl_id;
        if (!tmpl || tmpl.tint_role !== "base") {
            this.notification.add(
                _t("La línea seleccionada no es una base de pintura entintable."),
                { type: "warning" }
            );
            return;
        }
        if (!tmpl.tint_base_type_id || !tmpl.tint_size_id) {
            this.notification.add(
                _t("Esta base no tiene tipo o presentación configurados."),
                { type: "warning" }
            );
            return;
        }

        const payload = await makeAwaitable(this.dialog, TintFormulaPopup, {
            baseTypeId: tmpl.tint_base_type_id.id,
            sizeId: tmpl.tint_size_id.id,
        });
        if (!payload) {
            return;
        }

        // Se guarda como nota de cliente (texto plano y visible en la línea).
        line.setCustomerNote(payload.text);

        this.notification.add(_t("Entintado guardado en la línea."), { type: "success" });
    },
});
