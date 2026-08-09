/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { runTintFlow } from "@entintados_pdv/app/utils/tint_flow";

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
        const lineTmpl = line?.product_id?.product_tmpl_id;

        if (line && lineTmpl?.tint_role === "base") {
            // Ya hay una base seleccionada en la orden: se resuelve el
            // entintado sobre ella sin pedir de nuevo base ni presentación.
            const baseProduct = line.product_id;
            const qty = line.qty || 1;

            this.goBack();

            const parent = await runTintFlow(this, {
                baseProduct,
                replaceLine: line,
                qty,
                initialColorId: color.id,
            });

            if (parent) {
                order.uiState.selectedTintColor = null;
                order.uiState.selectedTintColorId = null;
            }
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