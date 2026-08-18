
import { Component, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { formatPoints } from "@entintados_pdv/app/utils/tint_points";
import {
    computeTintedPrice,
    formulaDoses,
} from "@entintados_pdv/app/utils/tint_order";
import { addTintedFromCard } from "@entintados_pdv/app/utils/tint_flow";
import { TintTable } from "@entintados_pdv/app/components/tint_table/tint_table";

/**
 * Panel de entintado. Permite seleccionar color, filtrar por galería,
 * presentación y tipo de base, y elegir la base a dispensar mediante TintTable.
 */
export class TintPanel extends Component {
    static template = "entintados_pdv.TintPanel";
    static components = { TintTable };
    static props = {};

    setup() {
        this.pos = usePos();
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            // Paso 1: color
            colorId: null,
            // Paso 2: filtros derivados del color
            galleryId: null,
            sizeId: null,
            baseTypeId: null,
            formulasVersion: 0,
            loadingFormulas: false,
        });
        this._loadedColorIds = new Set();
        // Modo debug para diagnósticos del catálogo.
        this.isDebug = Boolean(odoo.debug);
    }

    // Columnas de tablas

    get colorColumns() {
        return [
            { label: "Muestra", class: "o-tint-th-swatch" },
            { label: "Código", class: "text-nowrap" },
            { label: "Color" },
        ];
    }

    get baseColumns() {
        return [
            { label: "Base a dispensar" },
            { label: "Detalle" },
            { label: "Puntos", class: "text-end text-nowrap" },
            { label: "Precio", class: "text-end text-nowrap" },
        ];
    }

    // Diagnóstico del catálogo

    get catalogStats() {
        if (!this.isDebug) {
            return [];
        }
        const count = (model) => this.pos.models[model]?.getAll?.().length ?? null;
        return [
            { label: "Galerías", value: count("tint.gallery") },
            { label: "Presentaciones", value: count("tint.size") },
            { label: "Tipos de base", value: count("tint.base.type") },
            { label: "Colores", value: count("tint.color") },
            { label: "Fórmulas", value: count("tint.color.formula") },
            { label: "Dosis", value: count("tint.color.formula.line") },
        ];
    }

    /** Modelos no cargados en el cliente. */
    get missingModels() {
        return this.catalogStats.filter((stat) => stat.value === null);
    }

    /** Modelos registrados sin registros cargados desde el servidor. */
    get emptyModels() {
        return this.catalogStats.filter((stat) => stat.value === 0);
    }

    get galleriesLoaded() {
        return this.pos.models["tint.gallery"]?.getAll?.().length ?? 0;
    }

    /** Fórmulas sin galería asignada (solo evaluado en modo debug). */
    get formulasWithoutGallery() {
        if (!this.isDebug || !this.galleriesLoaded) {
            return 0;
        }
        return this.formulas.filter((formula) => !formula.gallery_id).length;
    }

    get hasCatalog() {
        return (this.pos.models["tint.color"]?.getAll?.() ?? []).some(
            (color) => color.has_formula
        );
    }

    // Catálogo base

    get formulas() {
        return this.pos.models["tint.color.formula"]?.getAll?.() ?? [];
    }

    get formulaCount() {
        return this.formulas.length;
    }

    // Paso 1: color

    /** Término de búsqueda obtenido del buscador nativo del POS. */
    get searchTerm() {
        return (this.pos.searchProductWord || "").trim().toLowerCase();
    }

    /** Colores con fórmula disponibles, filtrados por búsqueda (nombre/código). */
    get colors() {
        const term = this.searchTerm;
        return (this.pos.models["tint.color"]?.getAll?.() ?? [])
            .filter((color) => {
                if (!color.has_formula) {
                    return false;
                }
                if (term) {
                    return (
                        (color.name || "").toLowerCase().includes(term) ||
                        (color.code || "").toLowerCase().includes(term)
                    );
                }
                return true;
            })
            .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    }

    get selectedColor() {
        return this.state.colorId
            ? this.pos.models["tint.color"]?.get?.(this.state.colorId)
            : null;
    }

    async selectColor(id) {
        this.state.colorId = id;
        this.state.galleryId = null;
        this.state.sizeId = null;
        this.state.baseTypeId = null;
        this.pos.searchProductWord = "";
        await this.loadColorFormulas(id);
    }

    /** Carga bajo demanda las fórmulas del color indicado. */
    async loadColorFormulas(colorId) {
        if (!colorId || this._loadedColorIds.has(colorId)) {
            return;
        }
        this.state.loadingFormulas = true;
        try {
            await this.pos.data.callRelated("tint.color.formula", "get_pos_formulas", [
                this.pos.config.id,
                [["color_id", "=", colorId]],
            ]);
            this._loadedColorIds.add(colorId);
            this.state.formulasVersion++;
        } finally {
            this.state.loadingFormulas = false;
        }
    }

    clearColor() {
        Object.assign(this.state, {
            colorId: null,
            galleryId: null,
            sizeId: null,
            baseTypeId: null,
        });
        this.pos.searchProductWord = "";
    }

    // Paso 2: filtros en cascada

    /** Fórmulas asociadas al color seleccionado. */
    get colorFormulas() {
        // Reactividad ante carga bajo demanda.
        void this.state.formulasVersion;
        if (!this.state.colorId) {
            return [];
        }
        return this.formulas.filter((formula) => formula.color_id?.id === this.state.colorId);
    }

    /** Fórmulas filtradas según el nivel de filtro aplicado. */
    formulasUpTo(level) {
        const { galleryId, sizeId, baseTypeId } = this.state;
        return this.colorFormulas.filter((formula) => {
            if (level >= 1 && galleryId && formula.gallery_id?.id !== galleryId) {
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

    optionsFor(level, field, model, sorter) {
        const counts = new Map();
        for (const formula of this.formulasUpTo(level - 1)) {
            const record = formula[field];
            if (!record) {
                continue;
            }
            const baseCount = this.basesFor(
                formula.size_id?.id,
                formula.base_type_id?.id
            ).length;
            if (!baseCount) {
                continue;
            }
            counts.set(record.id, (counts.get(record.id) || 0) + baseCount);
        }
        return (this.pos.models[model]?.getAll?.() ?? [])
            .filter((record) => counts.has(record.id))
            .map((record) => ({ record, count: counts.get(record.id) }))
            .sort(sorter);
    }

    bySequence(a, b) {
        return (a.record.sequence || 0) - (b.record.sequence || 0);
    }

    get galleries() {
        return this.optionsFor(1, "gallery_id", "tint.gallery", this.bySequence);
    }

    get sizes() {
        return this.optionsFor(2, "size_id", "tint.size", this.bySequence);
    }

    get baseTypes() {
        return this.optionsFor(3, "base_type_id", "tint.base.type", this.bySequence);
    }

    get levels() {
        return [
            {
                key: "gallery",
                label: "Galería",
                options: this.galleries,
                selected: this.state.galleryId,
            },
            {
                key: "size",
                label: "Presentación",
                options: this.sizes,
                selected: this.state.sizeId,
            },
            {
                key: "baseType",
                label: "Tipo de base",
                options: this.baseTypes,
                selected: this.state.baseTypeId,
            },
        ];
    }

    // Paso 3: bases concretas

    /** Obtiene los productos base para una presentación y tipo de base específicos. */
    basesFor(sizeId, baseTypeId) {
        return this.pos.models["product.product"].getAll().filter((product) => {
            const tmpl = product.product_tmpl_id;
            return (
                tmpl?.tint_role === "base" &&
                tmpl.tint_size_id?.id === sizeId &&
                tmpl.tint_base_type_id?.id === baseTypeId
            );
        });
    }

    /** Indica si el filtro de galería está activo para condicionar su visibilidad en fila. */
    get galleryFilterActive() {
        return Boolean(this.state.galleryId);
    }

    /** Determina si se muestran las bases (requiere color seleccionado). */
    get showCards() {
        return Boolean(this.state.colorId);
    }

    /** Tarjetas de bases disponibles para las fórmulas filtradas, ordenadas y filtradas por búsqueda. */
    get cards() {
        if (!this.showCards) {
            return [];
        }
        const cards = [];
        for (const formula of this.formulasUpTo(3)) {
            for (const baseProduct of this.basesFor(formula.size_id?.id, formula.base_type_id?.id)) {
                cards.push(this.buildCard(formula, baseProduct));
            }
        }
        // Filtra bases por nombre, tipo de base, galería o presentación.
        const term = this.searchTerm;
        const filtered = term
            ? cards.filter(
                  (card) =>
                      (card.baseProduct.display_name || "").toLowerCase().includes(term) ||
                      (card.baseType?.name || "").toLowerCase().includes(term) ||
                      (card.gallery?.name || "").toLowerCase().includes(term) ||
                      (card.size?.name || "").toLowerCase().includes(term)
              )
            : cards;
        return filtered.sort(
            (a, b) =>
                (a.baseProduct.display_name || "").localeCompare(b.baseProduct.display_name || "") ||
                a.price - b.price
        );
    }

    buildCard(formula, baseProduct) {
        const doses = formulaDoses(this.pos, formula);
        return {
            key: `${formula.id}-${baseProduct.id}`,
            formula,
            baseProduct,
            color: formula.color_id,
            gallery: formula.gallery_id,
            baseType: formula.base_type_id,
            size: formula.size_id,
            doses,
            totalPoints: doses.reduce((acc, dose) => acc + dose.points, 0),
            price: computeTintedPrice(this.pos, baseProduct, formula),
        };
    }

    // Diagnóstico de resolución de bases

    comboLabel(size, baseType) {
        return [size?.name, baseType?.name]
            .map((part) => part || "(sin definir)")
            .join(" · ");
    }

    /** Combinaciones requeridas por las fórmulas del color sin producto base disponible. */
    get missingCombos() {
        const seen = new Map();
        for (const formula of this.formulasUpTo(3)) {
            if (this.basesFor(formula.size_id?.id, formula.base_type_id?.id).length) {
                continue;
            }
            const key = [formula.size_id?.id, formula.base_type_id?.id].join("-");
            if (!seen.has(key)) {
                seen.set(key, this.comboLabel(formula.size_id, formula.base_type_id));
            }
        }
        return [...seen.values()];
    }

    get unsellableCount() {
        if (!this.showCards) {
            return 0;
        }
        return this.formulasUpTo(3).filter(
            (f) => !this.basesFor(f.size_id?.id, f.base_type_id?.id).length
        ).length;
    }

    /** Productos base cargados en el POS con su combinación de presentación y tipo. */
    get loadedBases() {
        return this.pos.models["product.product"]
            .getAll()
            .filter((product) => product.product_tmpl_id?.tint_role === "base")
            .map((product) => {
                const tmpl = product.product_tmpl_id;
                return {
                    id: product.id,
                    name: product.display_name,
                    combo: this.comboLabel(tmpl.tint_size_id, tmpl.tint_base_type_id),
                };
            });
    }

    // Interacción

    selectLevel(key, id) {
        if (key === "gallery") {
            this.state.galleryId = this.state.galleryId === id ? null : id;
            this.state.sizeId = null;
            this.state.baseTypeId = null;
        } else if (key === "size") {
            this.state.sizeId = this.state.sizeId === id ? null : id;
            this.state.baseTypeId = null;
        } else {
            this.state.baseTypeId = this.state.baseTypeId === id ? null : id;
        }
    }

    clearFilters() {
        this.state.galleryId = null;
        this.state.sizeId = null;
        this.state.baseTypeId = null;
    }

    async addCard(card) {
        await addTintedFromCard(this, {
            baseProduct: card.baseProduct,
            formula: card.formula,
            color: card.color,
        });
    }

    // Formato

    formatPoints(points) {
        return formatPoints(points);
    }

    formatPrice(amount) {
        return this.env.utils?.formatCurrency
            ? this.env.utils.formatCurrency(amount)
            : String(amount);
    }
}
