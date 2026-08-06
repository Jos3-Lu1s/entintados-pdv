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
            initialColorId: order.uiState?.selectedTintColorId || false,
        });
        if (!payload) {
            return;
        }

        // 1. Guardar la especificación del entintado en la nota de cliente de la línea base
        line.setCustomerNote(payload.text);

        // 2. Agregar automáticamente los materiales / colorantes que componen la fórmula debajo de la base
        if (payload.doses && payload.doses.length > 0) {
            for (const dose of payload.doses) {
                if (dose.colorantId) {
                    const colorantProduct = this.pos.models["product.product"].get(dose.colorantId);
                    if (colorantProduct) {
                        const price = colorantProduct.price_per_point || colorantProduct.lst_price || 0;
                        const noteText = _t("Insumo de entintado para: %s (%s Pts)", line.product_id?.display_name || "", dose.points);

                        let addedLine = null;
                        if (typeof this.pos.addLineToCurrentOrder === "function") {
                            addedLine = await this.pos.addLineToCurrentOrder({
                                product_id: colorantProduct,
                                qty: dose.points,
                                price_unit: price,
                                customer_note: noteText,
                            });
                        } else if (this.pos.models && this.pos.models["pos.order.line"]) {
                            addedLine = this.pos.models["pos.order.line"].create({
                                order_id: order,
                                product_id: colorantProduct,
                                qty: dose.points,
                                price_unit: price,
                                customer_note: noteText,
                            });
                        } else if (typeof order?.add_product === "function") {
                            await order.add_product(colorantProduct, {
                                quantity: dose.points,
                                price: price,
                                customer_note: noteText,
                            });
                        }

                        if (addedLine && typeof addedLine.setCustomerNote === "function") {
                            addedLine.setCustomerNote(noteText);
                        }
                    }
                }
            }
        }

        this.notification.add(_t("Entintado y materiales agregados a la orden."), { type: "success" });
    },
});
