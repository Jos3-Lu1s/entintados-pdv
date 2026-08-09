/** @odoo-module **/

import { Component } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";

/**
 * Panel de entintado — ocupa el lugar de la grilla cuando la pestaña
 * «Entintados» está activa.
 *
 * Deliberadamente NO se apoya en la grilla del núcleo. No parchea
 * `productsToDisplay` ni `addProductToOrder`, y sus tarjetas no fingen ser
 * `product.template`. La pestaña oculta el contenedor nativo y renderiza
 * este componente en su lugar, así que los internos de la grilla pueden
 * cambiar entre versiones de Odoo sin afectar al entintado.
 *
 * Por ahora es un armazón: muestra el estado del catálogo cargado en caja
 * para poder verificar que el intercambio de paneles funciona y que los
 * modelos llegaron al POS. El filtrado escalonado y las tarjetas de fórmula
 * se construyen encima de esto.
 */
export class TintPanel extends Component {
    static template = "entintados_pdv.TintPanel";
    static props = {};

    setup() {
        this.pos = usePos();
    }

    /** Cuántos registros de cada modelo llegaron a la caja. */
    get catalogStats() {
        const count = (model) => this.pos.models[model]?.getAll?.().length ?? null;
        return [
            { label: "Esquemas", value: count("product.schema") },
            { label: "Presentaciones", value: count("tint.size") },
            { label: "Tipos de base", value: count("tint.base.type") },
            { label: "Colores", value: count("tint.color") },
            { label: "Fórmulas", value: count("tint.color.formula") },
            { label: "Dosis", value: count("tint.color.formula.line") },
        ];
    }

    /** Modelos que no se cargaron: delata un `_load_pos_data_models` incompleto. */
    get missingModels() {
        return this.catalogStats.filter((stat) => stat.value === null);
    }

    get formulaCount() {
        return this.pos.models["tint.color.formula"]?.getAll?.().length ?? 0;
    }
}
