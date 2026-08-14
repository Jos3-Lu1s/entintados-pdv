
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
 * este componente en su lugar.
 *
 * ## El recorrido: color → filtros → base concreta
 *
 * El color es el punto de partida. Elegido el color, los filtros (galería, presentación, tipo de
 * base) se calculan SOLO sobre las fórmulas de ese color y sirven para
 * acotar. Al final se ofrecen las BASES CONCRETAS (`product.product`): cada
 * envase vendible es su propia tarjeta, con su marca y su precio.
 */
export class TintPanel extends Component {
    static template = "entintados_pdv.TintPanel";
    static props = {};

    setup() {
        this.pos = usePos();
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            // Paso 1 — color
            colorId: null,
            search: "",
            collectionId: null,
            // Paso 2 — filtros (opcionales, derivados del color)
            galleryId: null,
            sizeId: null,
            baseTypeId: null,
        });
    }

    // --- Diagnóstico del catálogo ---------------------------------------

    get catalogStats() {
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

    get galleriesLoaded() {
        return this.pos.models["tint.gallery"]?.getAll?.().length ?? 0;
    }

    /**
     * Fórmulas sin galería asignada.
     *
     * Solo tiene sentido preguntarlo si hay galerías cargadas: en el cliente,
     * `gallery_id` es una relación que se resuelve contra los `tint.gallery`
     * en memoria, así que con cero cargadas TODA fórmula parece huérfana
     * aunque tenga galería en la base de datos.
     */
    get formulasWithoutGallery() {
        if (!this.galleriesLoaded) {
            return 0;
        }
        return this.formulas.filter((formula) => !formula.gallery_id).length;
    }

    get hasCatalog() {
        return this.formulas.length > 0 && !this.formulasWithoutGallery;
    }

    // --- Catálogo base ---------------------------------------------------

    get formulas() {
        return this.pos.models["tint.color.formula"]?.getAll?.() ?? [];
    }

    get formulaCount() {
        return this.formulas.length;
    }

    // --- Paso 1: color ---------------------------------------------------

    /** Ids de color que tienen al menos una fórmula registrada. */
    get colorIdsWithFormula() {
        const ids = new Set();
        for (const formula of this.formulas) {
            if (formula.color_id) {
                ids.add(formula.color_id.id);
            }
        }
        return ids;
    }

    get collections() {
        return this.pos.models["tint.collection"]?.getAll?.() ?? [];
    }

    get searchTerm() {
        return this.state.search.trim().toLowerCase();
    }

    /**
     * Colores ofrecibles: los que tienen fórmula, filtrados por búsqueda
     * (nombre o código) y por colección. Sin fórmula no se ofrecen porque no
     * habría ninguna base que dispensar.
     */
    get colors() {
        const withFormula = this.colorIdsWithFormula;
        const term = this.searchTerm;
        return (this.pos.models["tint.color"]?.getAll?.() ?? [])
            .filter((color) => {
                if (!withFormula.has(color.id)) {
                    return false;
                }
                if (this.state.collectionId) {
                    const collectionId = color.collection_id?.id || color.collection_id || false;
                    if (collectionId !== this.state.collectionId) {
                        return false;
                    }
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

    selectColor(id) {
        this.state.colorId = id;
        // Elegir color reinicia los filtros posteriores.
        this.state.galleryId = null;
        this.state.sizeId = null;
        this.state.baseTypeId = null;
    }

    clearColor() {
        Object.assign(this.state, {
            colorId: null,
            galleryId: null,
            sizeId: null,
            baseTypeId: null,
        });
    }

    selectCollection(id) {
        this.state.collectionId = this.state.collectionId === id ? null : id;
    }

    // --- Paso 2: filtros en cascada sobre el color -----------------------

    /** Fórmulas del color elegido. Base de todo el filtrado posterior. */
    get colorFormulas() {
        if (!this.state.colorId) {
            return [];
        }
        return this.formulas.filter((formula) => formula.color_id?.id === this.state.colorId);
    }

    /** Fórmulas del color que sobreviven a los filtros elegidos hasta ahora. */
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

    // --- Paso 3: bases concretas ----------------------------------------

    /**
     * Bases que sirven para una presentación y un tipo de base.
     *
     * La galería NO participa: identifica el origen de la receta, no el
     * envase. Aunque la fórmula venga del catálogo de la competencia, la base
     * que se dispensa y se cobra es la propia.
     */
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

    /** ¿El filtro de galería está activo? Decide si la galería se muestra en la tarjeta. */
    get galleryFilterActive() {
        return Boolean(this.state.galleryId);
    }

    /**
     * Se muestran las bases en cuanto hay color; los filtros solo acotan.
     */
    get showCards() {
        return Boolean(this.state.colorId);
    }

    /**
     * Tarjetas de BASE CONCRETA.
     *
     * Se itera sobre TODAS las bases de cada fórmula y se emite
     * una tarjeta por producto: cuando una combinación tiene varias marcas,
     * cada una aparece con su propio precio y el cajero elige.
     */
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
        return cards.sort(
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

    // --- Diagnóstico de la resolución de base ----------------------------

    comboLabel(size, baseType) {
        return [size?.name, baseType?.name]
            .map((part) => part || "(sin definir)")
            .join(" · ");
    }

    /** Combinaciones que las fórmulas del color piden y ningún producto base cubre. */
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

    /**
     * Bases que llegaron a la caja, con los dos atributos que las identifican.
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
                    combo: this.comboLabel(tmpl.tint_size_id, tmpl.tint_base_type_id),
                };
            });
    }

    // --- Interacción -----------------------------------------------------

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
