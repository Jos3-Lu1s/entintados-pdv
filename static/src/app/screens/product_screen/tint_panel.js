
import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { formatPoints } from "@entintados_pdv/app/utils/tint_points";
import {
    computeTintedPrice,
    computeTintedPriceDetails,
    formulaDoses,
} from "@entintados_pdv/app/utils/tint_order";
import { addTintedFromCard } from "@entintados_pdv/app/utils/tint_flow";
import { TintTable } from "@entintados_pdv/app/components/tint_table/tint_table";
import { TintCreateColorPopup } from "@entintados_pdv/app/components/tint_create_color_popup/tint_create_color_popup";

/**
 * Panel de entintado. Permite seleccionar color, filtrar por galería,
 * presentación y tipo de base, y elegir la base a dispensar mediante TintTable.
 */
export class TintPanel extends Component {
    static template = "entintados_pdv.TintPanel";
    static components = { TintTable };
    // Estado de navegación compartido con la barra de pestañas (ProductScreen).
    static props = { uiState: Object };

    setup() {
        this.pos = usePos();
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this._loadedColorIds = new Set();
        // Modo debug para diagnósticos del catálogo.
        this.isDebug = Boolean(odoo.debug);
    }

    /** Estado de navegación (galería, color, filtros), propiedad del padre. */
    get ui() {
        return this.props.uiState;
    }

    // Columnas de tablas

    get colorColumns() {
        return [
            { label: "Código", class: "text-nowrap", style: "width: 120px;" },
            { label: "Color" },
            { label: "Galería", class: "text-nowrap", style: "width: 160px;" },
            { label: "Bases disponibles" },
        ];
    }

    get baseColumns() {
        return [
            { label: "Código", class: "text-nowrap", style: "width: 120px;" },
            { label: "Base a dispensar" },
            { label: "Tipo de base", class: "text-nowrap", style: "width: 130px;" },
            { label: "Presentación", class: "text-nowrap", style: "width: 130px;" },
            { label: "Dosis fórmula", class: "text-end text-nowrap", style: "width: 130px;" },
            { label: "Precio", class: "text-end text-nowrap", style: "width: 120px;" },
        ];
    }

    /** Galería seleccionada (fija durante el listado de colores). */
    get selectedGallery() {
        return this.ui.galleryId
            ? this.pos.models["tint.gallery"]?.get?.(this.ui.galleryId)
            : null;
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

    // Paso 1: galería

    /** Galerías disponibles ordenadas por secuencia y nombre. */
    get galleryOptions() {
        return [...(this.pos.models["tint.gallery"]?.getAll?.() ?? [])].sort(
            (a, b) =>
                (a.sequence || 0) - (b.sequence || 0) ||
                (a.name || "").localeCompare(b.name || "")
        );
    }

    /** Selecciona la galería y carga los ids de sus colores. */
    async selectGallery(id) {
        this.ui.galleryId = id;
        this.ui.colorId = null;
        this.ui.sizeIds = [];
        this.ui.baseTypeIds = [];
        this.ui.galleryColorIds = [];
        this.pos.searchProductWord = "";
        await this.loadGalleryColors(id);
    }

    /** Carga los IDs de colores con fórmulas en la galería. */
    async loadGalleryColors(galleryId) {
        if (!galleryId) {
            return;
        }
        this.ui.loadingColors = true;
        try {
            const ids = await this.pos.data.call(
                "tint.color.formula",
                "get_color_ids_for_gallery",
                [galleryId]
            );
            this.ui.galleryColorIds = ids || [];
        } finally {
            this.ui.loadingColors = false;
        }
    }

    // Paso 2: color

    /** Término de búsqueda del POS. */
    get searchTerm() {
        return (this.pos.searchProductWord || "").trim().toLowerCase();
    }

    /** Colores disponibles en la galería filtrados por búsqueda. */
    get colors() {
        if (!this.ui.galleryId) {
            return [];
        }
        const term = this.searchTerm;
        const allowed = new Set(this.ui.galleryColorIds);
        return (this.pos.models["tint.color"]?.getAll?.() ?? [])
            .filter((color) => {
                if (!allowed.has(color.id)) {
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
        return this.ui.colorId
            ? this.pos.models["tint.color"]?.get?.(this.ui.colorId)
            : null;
    }

    async selectColor(id) {
        this.ui.colorId = id;
        // Conserva la galería y reinicia filtros de presentación y tipo de base.
        this.ui.sizeIds = [];
        this.ui.baseTypeIds = [];
        this.pos.searchProductWord = "";
        await this.loadColorFormulas(id);
    }

    /** Carga bajo demanda las fórmulas del color indicado. */
    async loadColorFormulas(colorId) {
        if (!colorId || this._loadedColorIds.has(colorId)) {
            return;
        }
        this.ui.loadingFormulas = true;
        try {
            await this.pos.data.callRelated("tint.color.formula", "get_pos_formulas", [
                this.pos.config.id,
                [["color_id", "=", colorId]],
            ]);
            this._loadedColorIds.add(colorId);
            this.ui.formulasVersion++;
        } finally {
            this.ui.loadingFormulas = false;
        }
    }

    /** Limpia la selección de color y sus filtros conservando la galería. */
    clearColor() {
        Object.assign(this.ui, {
            colorId: null,
            sizeIds: [],
            baseTypeIds: [],
        });
        this.pos.searchProductWord = "";
    }

    /** Limpia la selección de galería y regresa al paso 1. */
    changeGallery() {
        Object.assign(this.ui, {
            galleryId: null,
            colorId: null,
            sizeIds: [],
            baseTypeIds: [],
            galleryColorIds: [],
        });
        this.pos.searchProductWord = "";
    }

    /** Abre el modal para crear nuevo color desde el panel. */
    async onClickCreateColor() {
        const payload = await makeAwaitable(this.dialog, TintCreateColorPopup, {
            galleryId: this.ui.galleryId || false,
        });
        if (payload?.colorId) {
            if (payload.galleryId && this.ui.galleryId !== payload.galleryId) {
                this.ui.galleryId = payload.galleryId;
            }
            if (this.ui.galleryId) {
                await this.loadGalleryColors(this.ui.galleryId);
            }
            this.ui.colorId = payload.colorId;
            await this.loadColorFormulas(payload.colorId);
        }
    }

    // Paso 3: filtros en cascada de la base

    /** Fórmulas asociadas al color seleccionado. */
    get colorFormulas() {
        // Reactividad ante carga bajo demanda.
        void this.ui.formulasVersion;
        if (!this.ui.colorId) {
            return [];
        }
        return this.formulas.filter((formula) => formula.color_id?.id === this.ui.colorId);
    }

    /** Fórmulas filtradas según el nivel de filtro aplicado. */
    formulasUpTo(level) {
        const { galleryId, sizeIds, baseTypeIds } = this.ui;
        const activeSizes = sizeIds || [];
        const activeBaseTypes = baseTypeIds || [];
        return this.colorFormulas.filter((formula) => {
            if (level >= 1 && galleryId && formula.gallery_id?.id !== galleryId) {
                return false;
            }
            if (level >= 2 && activeSizes.length && !activeSizes.includes(formula.size_id?.id)) {
                return false;
            }
            if (level >= 3 && activeBaseTypes.length && !activeBaseTypes.includes(formula.base_type_id?.id)) {
                return false;
            }
            return true;
        });
    }

    optionsFor(level, field, model, sorter) {
        const counts = new Map();
        const selectedIds = field === "size_id" ? (this.ui.sizeIds || []) : (this.ui.baseTypeIds || []);
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
            .filter((record) => counts.has(record.id) || selectedIds.includes(record.id))
            .map((record) => ({ record, count: counts.get(record.id) || 0 }))
            .sort(sorter);
    }

    bySequence(a, b) {
        return (a.record.sequence || 0) - (b.record.sequence || 0);
    }

    get sizes() {
        return this.optionsFor(2, "size_id", "tint.size", this.bySequence);
    }

    get baseTypes() {
        return this.optionsFor(3, "base_type_id", "tint.base.type", this.bySequence);
    }

    get hasActiveFilters() {
        return Boolean(this.ui.sizeIds?.length || this.ui.baseTypeIds?.length);
    }

    get levels() {
        // Niveles de filtro dentro del color seleccionado.
        const allLevels = [
            {
                key: "size",
                label: "Presentación",
                options: this.sizes,
                selectedIds: this.ui.sizeIds || [],
            },
            {
                key: "baseType",
                label: "Tipo de base",
                options: this.baseTypes,
                selectedIds: this.ui.baseTypeIds || [],
            },
        ];
        return allLevels.filter((level) => level.options.length > 0);
    }

    isOptionSelected(levelKey, recordId) {
        if (levelKey === "size") {
            return (this.ui.sizeIds || []).includes(recordId);
        }
        if (levelKey === "baseType") {
            return (this.ui.baseTypeIds || []).includes(recordId);
        }
        return false;
    }

    // Paso 3: bases concretas

    /** Obtiene productos base para una presentación y tipo de base. */
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

    /** Indica si hay una galería seleccionada. */
    get galleryFilterActive() {
        return Boolean(this.ui.galleryId);
    }

    /** Determina si se muestran las bases según si hay un color seleccionado. */
    get showCards() {
        return Boolean(this.ui.colorId);
    }

    /** Tarjetas de bases disponibles para las fórmulas y filtros activos. */
    get cards() {
        if (!this.showCards || !this.hasActiveFilters) {
            return [];
        }
        const cards = [];
        for (const formula of this.formulasUpTo(3)) {
            for (const baseProduct of this.basesFor(formula.size_id?.id, formula.base_type_id?.id)) {
                cards.push(this.buildCard(formula, baseProduct));
            }
        }
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
        const priceDetails = computeTintedPriceDetails(this.pos, baseProduct, formula);
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
            price: priceDetails.finalPrice,
            theoreticalPrice: priceDetails.theoreticalPrice,
            priceStatus: priceDetails.status,
            range: priceDetails.range,
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
        if (!this.hasActiveFilters) {
            return [];
        }
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
        if (!this.showCards || !this.hasActiveFilters) {
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
        if (key === "size") {
            const current = new Set(this.ui.sizeIds || []);
            if (current.has(id)) {
                current.delete(id);
            } else {
                current.add(id);
            }
            this.ui.sizeIds = Array.from(current);
        } else if (key === "baseType") {
            const current = new Set(this.ui.baseTypeIds || []);
            if (current.has(id)) {
                current.delete(id);
            } else {
                current.add(id);
            }
            this.ui.baseTypeIds = Array.from(current);
        }
    }

    clearFilters() {
        // Reinicia los filtros de presentación y tipo de base.
        this.ui.sizeIds = [];
        this.ui.baseTypeIds = [];
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
