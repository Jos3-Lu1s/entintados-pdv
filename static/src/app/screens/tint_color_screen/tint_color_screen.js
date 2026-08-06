/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { TintFormulaPopup } from "@entintados_pdv/js/tint_formula_popup";

export class TintColorScreen extends Component {
    static template = "entintados_pdv.TintColorScreen";

    /*
     * El router puede enviar propiedades adicionales.
     * No limites las props solamente a orderUuid.
     */
    static props = ["*"];

    setup() {
        this.pos = usePos();
        this.notification = useService("notification");
        this.dialog = useService("dialog");

        this.state = useState({
            search: "",
            collectionId: null,
        });

        console.log("[ENTINTADOS] TintColorScreen inicializada");
    }

    get colors() {
        const model = this.pos.models["tint.color"];

        if (!model) {
            console.warn("[ENTINTADOS] tint.color no fue cargado");
            return [];
        }

        const colors = model.getAll();
        const search = this.state.search.trim().toLocaleLowerCase();

        return colors.filter((color) => {
            const name = String(color.name || "").toLocaleLowerCase();
            const code = String(color.code || "").toLocaleLowerCase();
            const collectionId =
                color.collection_id?.id || color.collection_id || false;

            const matchesSearch =
                !search || name.includes(search) || code.includes(search);

            const matchesCollection =
                !this.state.collectionId ||
                collectionId === this.state.collectionId;

            return matchesSearch && matchesCollection;
        });
    }

    get collections() {
        const model = this.pos.models["tint.collection"];
        return model?.getAll?.() || [];
    }

    selectCollection(collectionId) {
        this.state.collectionId = collectionId;
    }

    clearCollection() {
        this.state.collectionId = null;
    }

    async selectColor(color) {
        const order = this.pos.getOrder();

        if (!order) {
            return;
        }

        order.uiState.selectedTintColor = color;
        order.uiState.selectedTintColorId = color.id;
        order.uiState.selectedTintColorName = color.name;
        order.uiState.selectedTintColorCode = color.code;

        const line = order.getSelectedOrderline();
        const lineTmpl = line?.product_id?.product_tmpl_id || line?.product_id;

        if (line && lineTmpl && lineTmpl.tint_role === "base") {
            // Base line is already selected in order -> Open TintFormulaPopup directly
            const baseTypeId =
                lineTmpl.tint_base_type_id?.id || lineTmpl.tint_base_type_id;
            const sizeId =
                lineTmpl.tint_size_id?.id || lineTmpl.tint_size_id;

            this.goBack();

            const payload = await makeAwaitable(this.dialog, TintFormulaPopup, {
                baseTypeId: baseTypeId,
                sizeId: sizeId,
                initialColorId: color.id,
            });

            if (!payload) return;

            line.setCustomerNote(payload.text);
            if (payload.doses && payload.doses.length > 0) {
                for (const dose of payload.doses) {
                    if (dose.colorantId) {
                        const colorantProduct =
                            this.pos.models["product.product"].get(
                                dose.colorantId
                            );
                        if (colorantProduct) {
                            const price =
                                colorantProduct.price_per_point ||
                                colorantProduct.lst_price ||
                                0;
                            const noteText = _t(
                                "Insumo de entintado para: %s (%s Pts)",
                                line.product_id?.display_name || "",
                                dose.points
                            );
                            await this.pos.addLineToCurrentOrder({
                                product_id: colorantProduct,
                                qty: dose.points,
                                price_unit: price,
                                customer_note: noteText,
                            });
                        }
                    }
                }
            }
            this.notification.add(
                _t("Entintado y materiales agregados a la orden."),
                { type: "success" }
            );
        } else {
            // No base line selected -> Store active color and return to ProductScreen
            this.notification.add(
                _t(
                    "Color %s seleccionado. Ahora selecciona una base de pintura entintable.",
                    color.code ? `[${color.code}] ${color.name}` : color.name
                ),
                { type: "info" }
            );
            this.goBack();
        }
    }

    goBack() {
        const order = this.pos.getOrder();

        if (!order) {
            return;
        }

        this.pos.navigate("ProductScreen", {
            orderUuid: order.uuid,
        });
    }
}

registry.category("pos_pages").add("TintColorScreen", {
    component: TintColorScreen,
    route: `/pos/ui/${odoo.pos_config_id}/tint-colors/{string:orderUuid}`,
    params: {
        orderUuid: true,
    },
});

console.log("[ENTINTADOS] TintColorScreen registrada");