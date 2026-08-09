/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { formatPoints } from "@entintados_pdv/app/utils/tint_points";
import {
    computeTintedPrice,
    formulaDoses,
} from "@entintados_pdv/app/utils/tint_order";
import { addTintedFromCard } from "@entintados_pdv/app/utils/tint_flow";

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
 * Todo el filtrado se deriva de `tint.color.formula`, que ya trae `scheme_id`,
 * `size_id` y `base_type_id`. Cada nivel se calcula sobre las fórmulas que
 * sobrevivieron al nivel anterior, así que nunca se ofrece una combinación
 * sin fórmula registrada.
 */
export class TintPanel extends Component {
    static template = "entintados_pdv.TintPanel";
    static props = {};

    setup() {
        this.pos = usePos();
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            schemaId: null,
            sizeId: null,
            baseTypeId: null,
            search: "",
        });
    }

    // --- Diagnóstico del catálogo ---------------------------------------

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

    /** Modelos que ni siquiera existen en el cliente. */
    get missingModels() {
        return this.catalogStats.filter((stat) => stat.value === null);
    }

    /**
     * Modelos registrados pero sin registros.
     *
     * Se distingue de `missingModels` a propósito: «no existe» apunta a
     * `_load_pos_data_models` o al registro en `tint_models.js`, mientras que
     * «existe y está vacío» apunta al servidor.
     */
    get emptyModels() {
        return this.catalogStats.filter((stat) => stat.value === 0);
    }

    get schemasLoaded() {
        return this.pos.models["product.schema"]?.getAll?.().length ?? 0;
    }

    /**
     * Fórmulas sin esquema asignado.
     *
     * Solo tiene sentido preguntarlo si hay esquemas cargados: en el cliente,
     * `scheme_id` es una relación que se resuelve contra los `product.schema`
     * en memoria, así que con cero cargados TODA fórmula parece huérfana
     * aunque su color tenga esquema en la base de datos. Avisar en ese caso
     * mandaría a corregir un dato que probablemente está bien.
     */
    get formulasWithoutSchema() {
        if (!this.schemasLoaded) {
            return 0;
        }
        return this.formulas.filter((formula) => !formula.scheme_id).length;
    }

    get hasCatalog() {
        return this.formulas.length > 0 && !this.formulasWithoutSchema;
    }

    // --- Filtrado en cascada --------------------------------------------

    get formulas() {
        return this.pos.models["tint.color.formula"]?.getAll?.() ?? [];
    }

    get formulaCount() {
        return this.formulas.length;
    }

    /** Fórmulas que sobreviven a los filtros elegidos hasta ahora. */
    formulasUpTo(level) {
        const { schemaId, sizeId, baseTypeId } = this.state;
        return this.formulas.filter((formula) => {
            if (level >= 1 && schemaId && formula.scheme_id?.id !== schemaId) {
                return false;
            }
            if (level >= 2 && sizeId && formula.size_id?.id !== sizeId) {
                return false;
            }
            if (level >= 3 && baseTypeId && formula.base_type_id?.id !== baseTypeId) {
                return false;
            }
            return true;
        });
    }

    /** Opciones de un nivel, con cuántas fórmulas hay detrás de cada una. */
    optionsFor(level, field, model, sorter) {
        const counts = new Map();
        for (const formula of this.formulasUpTo(level - 1)) {
            const record = formula[field];
            if (record) {
                counts.set(record.id, (counts.get(record.id) || 0) + 1);
            }
        }
        return (this.pos.models[model]?.getAll?.() ?? [])
            .filter((record) => counts.has(record.id))
            .map((record) => ({ record, count: counts.get(record.id) }))
            .sort(sorter);
    }

    get schemas() {
        return this.optionsFor(1, "scheme_id", "product.schema", (a, b) =>
            (a.record.name || "").localeCompare(b.record.name || "")
        );
    }

    get sizes() {
        return this.optionsFor(2, "size_id", "tint.size", (a, b) =>
            (a.record.sequence || 0) - (b.record.sequence || 0)
        );
    }

    get baseTypes() {
        return this.optionsFor(3, "base_type_id", "tint.base.type", (a, b) =>
            (a.record.sequence || 0) - (b.record.sequence || 0)
        );
    }

    get searchTerm() {
        return this.state.search.trim().toLowerCase();
    }

    /**
     * Con miles de fórmulas no se pinta nada hasta acorralar la combinación.
     * La búsqueda por color es el atajo para cuando el cliente llega con el
     * color en la mano.
     */
    get showCards() {
        return Boolean(this.state.baseTypeId) || this.searchTerm.length >= 2;
    }

    get cards() {
        if (!this.showCards) {
            return [];
        }
        const term = this.searchTerm;
        return this.formulasUpTo(3)
            .filter((formula) => {
                if (!term) {
                    return true;
                }
                const color = formula.color_id;
                return (
                    (color?.name || "").toLowerCase().includes(term) ||
                    (color?.code || "").toLowerCase().includes(term)
                );
            })
            .map((formula) => this.buildCard(formula))
            .filter((card) => card.baseProduct)
            .sort((a, b) => (a.color?.name || "").localeCompare(b.color?.name || ""));
    }

    buildCard(formula) {
        const baseProduct = this.resolveBaseProduct(formula);
        const doses = formulaDoses(this.pos, formula);
        return {
            formula,
            baseProduct,
            color: formula.color_id,
            doses,
            totalPoints: doses.reduce((acc, dose) => acc + dose.points, 0),
            price: baseProduct ? computeTintedPrice(this.pos, baseProduct, formula) : 0,
        };
    }

    /** El producto base concreto que corresponde a la combinación. */
    resolveBaseProduct(formula) {
        const schemaId = formula.scheme_id?.id;
        const sizeId = formula.size_id?.id;
        const baseTypeId = formula.base_type_id?.id;
        return this.pos.models["product.product"].getAll().find((product) => {
            const tmpl = product.product_tmpl_id;
            return (
                tmpl?.tint_role === "base" &&
                tmpl.tint_schema_id?.id === schemaId &&
                tmpl.tint_size_id?.id === sizeId &&
                tmpl.tint_base_type_id?.id === baseTypeId
            );
        });
    }

    /**
     * Fórmulas filtradas que no tienen producto base capturado.
     *
     * No se pueden vender, así que se ocultan de las tarjetas — pero callarlo
     * dejaría al cajero buscando un color que existe en la carta y no aparece.
     */
    get unsellableCount() {
        if (!this.showCards) {
            return 0;
        }
        return this.formulasUpTo(3).filter((f) => !this.resolveBaseProduct(f)).length;
    }

    /** Etiqueta legible de una combinación (esquema · presentación · base). */
    comboLabel(schema, size, baseType) {
        return [schema?.name, size?.name, baseType?.name]
            .map((part) => part || "(sin definir)")
            .join(" · ");
    }

    /**
     * Combinaciones que las fórmulas piden y ningún producto base cubre.
     *
     * Se muestra junto a las bases que el POS sí conoce: el desajuste casi
     * siempre salta a la vista comparando ambas listas.
     */
    get missingCombos() {
        const seen = new Map();
        for (const formula of this.formulasUpTo(3)) {
            if (this.resolveBaseProduct(formula)) {
                continue;
            }
            const key = [
                formula.scheme_id?.id,
                formula.size_id?.id,
                formula.base_type_id?.id,
            ].join("-");
            if (!seen.has(key)) {
                seen.set(
                    key,
                    this.comboLabel(
                        formula.scheme_id,
                        formula.size_id,
                        formula.base_type_id
                    )
                );
            }
        }
        return [...seen.values()];
    }

    /**
     * Bases que llegaron a la caja, con los tres atributos que las identifican.
     *
     * Si esta lista sale vacía, el problema no son los atributos sino que los
     * productos no llegan al POS: revisar «Disponible en PdV» y «Puede
     * venderse» en la ficha.
     */
    get loadedBases() {
        return this.pos.models["product.product"]
            .getAll()
            .filter((product) => product.product_tmpl_id?.tint_role === "base")
            .map((product) => {
                const tmpl = product.product_tmpl_id;
                return {
                    id: product.id,
                    name: product.display_name,
                    combo: this.comboLabel(
                        tmpl.tint_schema_id,
                        tmpl.tint_size_id,
                        tmpl.tint_base_type_id
                    ),
                };
            });
    }

    // --- Interacción -----------------------------------------------------

    selectSchema(id) {
        this.state.schemaId = this.state.schemaId === id ? null : id;
        this.state.sizeId = null;
        this.state.baseTypeId = null;
    }

    selectSize(id) {
        this.state.sizeId = this.state.sizeId === id ? null : id;
        this.state.baseTypeId = null;
    }

    selectBaseType(id) {
        this.state.baseTypeId = this.state.baseTypeId === id ? null : id;
    }

    clearFilters() {
        Object.assign(this.state, {
            schemaId: null,
            sizeId: null,
            baseTypeId: null,
            search: "",
        });
    }

    async addCard(card) {
        await addTintedFromCard(this, {
            baseProduct: card.baseProduct,
            formula: card.formula,
            color: card.color,
        });
    }

    // --- Formato ---------------------------------------------------------

    formatPoints(points) {
        return formatPoints(points);
    }

    formatPrice(amount) {
        return this.env.utils?.formatCurrency
            ? this.env.utils.formatCurrency(amount)
            : String(amount);
    }
}
