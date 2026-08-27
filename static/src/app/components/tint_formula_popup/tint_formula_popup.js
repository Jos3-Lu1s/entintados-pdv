import { Component, useState, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { formatPoints } from "@entintados_pdv/app/utils/tint_points";
import { formulaDoses } from "@entintados_pdv/app/utils/tint_order";

/**
 * Diálogo para seleccionar el color y fórmula sobre una base, o registrar un nuevo color/fórmula.
 */
export class TintFormulaPopup extends Component {
    static template = "entintados_pdv.TintFormulaPopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        baseTypeId: { type: [Number, Boolean], optional: true },
        sizeId: { type: [Number, Boolean], optional: true },
        initialColorId: { type: [Number, Boolean], optional: true },
        getPayload: { type: Function, optional: true },
        close: Function,
    };
    static defaultProps = {
        title: _t("Configurar entintado"),
        baseTypeId: false,
        sizeId: false,
        initialColorId: false,
    };

    setup() {
        this.pos = useService("pos");
        const galleries = this.pos.models["tint.gallery"]?.getAll() || [];
        const defaultGallery = [...galleries].sort((a, b) => (a.sequence || 0) - (b.sequence || 0))[0];

        this.state = useState({
            activeTab: "tint", // "tint" | "create_color"
            search: "",
            colorId: this.props.initialColorId || false,
            extractionDone: false,

            // Datos para nuevo color (tint.color)
            newColorName: "",
            newColorCode: "",
            newColorNotes: "",

            // Galería, base y presentación para la fórmula
            newGalleryId: defaultGallery ? String(defaultGallery.id) : "",
            newBaseTypeId: this.props.baseTypeId ? String(this.props.baseTypeId) : "",
            newSizeId: this.props.sizeId ? String(this.props.sizeId) : "",

            // Líneas de fórmula y dosis
            selectedColorantId: "",
            newColorantPoints: 1,
            newFormulaLines: [],

            isCreatingColor: false,
            createColorError: "",
            createColorSuccess: "",
        });

        // Carga bajo demanda de fórmulas para la base y presentación.
        onWillStart(async () => {
            await this.loadFormulasForLine();
        });
    }

    async loadFormulasForLine() {
        if (!this.props.baseTypeId || !this.props.sizeId) {
            return;
        }
        await this.pos.data.callRelated("tint.color.formula", "get_pos_formulas", [
            this.pos.config.id,
            [
                ["base_type_id", "=", this.props.baseTypeId],
                ["size_id", "=", this.props.sizeId],
            ],
        ]);
    }

    setTab(tab) {
        this.state.activeTab = tab;
        this.state.createColorError = "";
        this.state.createColorSuccess = "";
    }

    get galleries() {
        if (!this.pos.models["tint.gallery"]) {
            return [];
        }
        return [...this.pos.models["tint.gallery"].getAll()]
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0) || (a.name || "").localeCompare(b.name || ""));
    }

    get baseTypes() {
        if (!this.pos.models["tint.base.type"]) {
            return [];
        }
        return [...this.pos.models["tint.base.type"].getAll()]
            .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    }

    get sizes() {
        if (!this.pos.models["tint.size"]) {
            return [];
        }
        return [...this.pos.models["tint.size"].getAll()]
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
    }

    get colorants() {
        if (!this.pos.models["product.product"]) {
            return [];
        }
        return [...this.pos.models["product.product"].getAll()]
            .filter((p) => p.tint_role === "colorant")
            .sort((a, b) => (a.display_name || a.name || "").localeCompare(b.display_name || b.name || ""));
    }

    get newCapacityPoints() {
        const baseTypeId = parseInt(this.state.newBaseTypeId);
        const sizeId = parseInt(this.state.newSizeId);
        if (!baseTypeId || !sizeId || !this.pos.models["tint.base.capacity"]) {
            return 0;
        }
        const cap = this.pos.models["tint.base.capacity"]
            .getAll()
            .find(
                (c) =>
                    c.base_type_id?.id === baseTypeId &&
                    c.size_id?.id === sizeId
            );
        return cap ? cap.max_points : 0;
    }

    get newFormulaTotalPoints() {
        return this.state.newFormulaLines.reduce((acc, l) => acc + (l.points || 0), 0);
    }

    get newFormulaCostMin() {
        return this.state.newFormulaLines.reduce((acc, l) => {
            const colorant = this.pos.models["product.product"].get(l.colorantId);
            const cost = colorant?.standard_price || 0;
            return acc + (l.points || 0) * cost;
        }, 0);
    }

    get newFormulaCostMax() {
        return this.state.newFormulaLines.reduce((acc, l) => {
            const colorant = this.pos.models["product.product"].get(l.colorantId);
            const price = colorant?.lst_price ?? colorant?.list_price ?? colorant?.product_tmpl_id?.list_price ?? 0;
            return acc + (l.points || 0) * price;
        }, 0);
    }

    get selectedFormulaCostMin() {
        if (!this.selectedFormula) return 0;
        if (typeof this.selectedFormula.cost_min === "number" && this.selectedFormula.cost_min > 0) {
            return this.selectedFormula.cost_min;
        }
        return this.doses.reduce((acc, dose) => {
            const colorant = this.pos.models["product.product"].get(dose.colorantId || dose.id);
            const cost = colorant?.standard_price || 0;
            return acc + (dose.points || 0) * cost;
        }, 0);
    }

    get selectedFormulaCostMax() {
        if (!this.selectedFormula) return 0;
        if (typeof this.selectedFormula.cost_max === "number" && this.selectedFormula.cost_max > 0) {
            return this.selectedFormula.cost_max;
        }
        return this.doses.reduce((acc, dose) => {
            const colorant = this.pos.models["product.product"].get(dose.colorantId || dose.id);
            const price = colorant?.lst_price ?? colorant?.list_price ?? colorant?.product_tmpl_id?.list_price ?? 0;
            return acc + (dose.points || 0) * price;
        }, 0);
    }

    formatCurrency(amount) {
        if (this.env.utils?.formatCurrency) {
            return this.env.utils.formatCurrency(amount);
        }
        return "$" + Number(amount || 0).toFixed(2);
    }

    addColorantLine() {
        const colorantId = parseInt(this.state.selectedColorantId);
        const points = parseFloat(this.state.newColorantPoints);
        if (!colorantId || isNaN(colorantId)) {
            return;
        }
        if (!points || points <= 0 || isNaN(points)) {
            return;
        }
        const colorant = this.pos.models["product.product"].get(colorantId);
        if (!colorant) {
            return;
        }

        const existing = this.state.newFormulaLines.find((l) => l.colorantId === colorantId);
        if (existing) {
            existing.points = Number((existing.points + points).toFixed(4));
        } else {
            this.state.newFormulaLines.push({
                colorantId,
                colorantName: colorant.display_name || colorant.name,
                points,
            });
        }
        this.state.selectedColorantId = "";
        this.state.newColorantPoints = 1;
    }

    removeColorantLine(index) {
        this.state.newFormulaLines.splice(index, 1);
    }

    async saveNewColor() {
        if (!this.state.newColorName.trim()) {
            this.state.createColorError = _t("El nombre del color es obligatorio.");
            return;
        }

        const galleryId = parseInt(this.state.newGalleryId);
        const baseTypeId = parseInt(this.state.newBaseTypeId);
        const sizeId = parseInt(this.state.newSizeId);

        if (this.state.newFormulaLines.length > 0) {
            if (!galleryId || !baseTypeId || !sizeId) {
                this.state.createColorError = _t("Para registrar la fórmula debes seleccionar la Galería, el Tipo de Base y la Presentación.");
                return;
            }
            if (this.newCapacityPoints > 0 && this.newFormulaTotalPoints > this.newCapacityPoints) {
                this.state.createColorError = _t(
                    "La dosis total de la fórmula (%s) excede la capacidad del envase (%s).",
                    this.formatPoints(this.newFormulaTotalPoints),
                    this.formatPoints(this.newCapacityPoints)
                );
                return;
            }
        }

        this.state.isCreatingColor = true;
        this.state.createColorError = "";
        this.state.createColorSuccess = "";

        try {
            // 1. Crear tint.color
            const vals = {
                name: this.state.newColorName.trim(),
            };
            if (this.state.newColorCode.trim()) {
                vals.code = this.state.newColorCode.trim();
            }
            if (this.state.newColorNotes.trim()) {
                vals.notes = this.state.newColorNotes.trim();
            }

            let colorRecord = null;
            if (this.pos.data && typeof this.pos.data.create === "function") {
                const res = await this.pos.data.create("tint.color", [vals]);
                colorRecord = Array.isArray(res) ? res[0] : res;
            } else if (this.pos.orm && typeof this.pos.orm.create === "function") {
                const ids = await this.pos.orm.create("tint.color", [vals]);
                const createdId = Array.isArray(ids) ? ids[0] : ids;
                colorRecord = this.pos.models["tint.color"]?.get(createdId);
            }

            const colorId = colorRecord ? (colorRecord.id || colorRecord) : false;

            // 2. Crear fórmula y líneas de dosis si fueron configuradas
            if (colorId && this.state.newFormulaLines.length > 0 && galleryId && baseTypeId && sizeId) {
                const formulaVals = {
                    color_id: colorId,
                    gallery_id: galleryId,
                    base_type_id: baseTypeId,
                    size_id: sizeId,
                };
                let formulaRecord = null;
                if (this.pos.data && typeof this.pos.data.create === "function") {
                    const fRes = await this.pos.data.create("tint.color.formula", [formulaVals]);
                    formulaRecord = Array.isArray(fRes) ? fRes[0] : fRes;
                } else if (this.pos.orm && typeof this.pos.orm.create === "function") {
                    const fIds = await this.pos.orm.create("tint.color.formula", [formulaVals]);
                    const fId = Array.isArray(fIds) ? fIds[0] : fIds;
                    formulaRecord = this.pos.models["tint.color.formula"]?.get(fId);
                }

                const formulaId = formulaRecord ? (formulaRecord.id || formulaRecord) : false;

                if (formulaId) {
                    const lineValsList = this.state.newFormulaLines.map((l, idx) => ({
                        formula_id: formulaId,
                        colorant_id: l.colorantId,
                        points: l.points,
                        sequence: (idx + 1) * 10,
                    }));
                    if (this.pos.data && typeof this.pos.data.create === "function") {
                        await this.pos.data.create("tint.color.formula.line", lineValsList);
                    } else if (this.pos.orm && typeof this.pos.orm.create === "function") {
                        await this.pos.orm.create("tint.color.formula.line", lineValsList);
                    }
                }
            }

            this.state.createColorSuccess = _t("¡Color y fórmula registrados exitosamente!");
            this.state.newColorName = "";
            this.state.newColorCode = "";
            this.state.newColorNotes = "";
            const galleries = this.pos.models["tint.gallery"]?.getAll() || [];
            const defaultGallery = [...galleries].sort((a, b) => (a.sequence || 0) - (b.sequence || 0))[0];
            this.state.newGalleryId = defaultGallery ? String(defaultGallery.id) : "";
            this.state.newBaseTypeId = this.props.baseTypeId ? String(this.props.baseTypeId) : "";
            this.state.newSizeId = this.props.sizeId ? String(this.props.sizeId) : "";
            this.state.newFormulaLines = [];
            this.state.selectedColorantId = "";
            this.state.newColorantPoints = 1;

            if (colorId) {
                this.state.colorId = colorId;
                setTimeout(() => {
                    this.state.activeTab = "tint";
                    this.state.createColorSuccess = "";
                }, 1200);
            }
        } catch (error) {
            console.error("Error al crear el color/fórmula:", error);
            this.state.createColorError =
                error?.data?.message ||
                error?.message ||
                _t("No se pudo crear el color/fórmula. Verifica los datos introducidos.");
        } finally {
            this.state.isCreatingColor = false;
        }
    }

    formatPoints(total) {
        return formatPoints(total);
    }

    get baseType() {
        return this.props.baseTypeId
            ? this.pos.models["tint.base.type"].get(this.props.baseTypeId)
            : null;
    }

    get size() {
        return this.props.sizeId
            ? this.pos.models["tint.size"].get(this.props.sizeId)
            : null;
    }

    /** Capacidad máxima en puntos para la base y presentación seleccionadas. */
    get capacityPoints() {
        const cap = this.pos.models["tint.base.capacity"]
            .getAll()
            .find(
                (c) =>
                    c.base_type_id?.id === this.props.baseTypeId &&
                    c.size_id?.id === this.props.sizeId
            );
        return cap ? cap.max_points : 0;
    }

    /** Fórmulas compatibles con la base y presentación actuales. */
    get formulasForLine() {
        return this.pos.models["tint.color.formula"]
            .getAll()
            .filter(
                (f) =>
                    f.base_type_id?.id === this.props.baseTypeId &&
                    f.size_id?.id === this.props.sizeId
            );
    }

    /** Colores disponibles para la base y presentación actuales, filtrados por búsqueda. */
    get availableColors() {
        const term = this.state.search.trim().toLowerCase();
        const formulaColors = this.formulasForLine.map((f) => f.color_id).filter((c) => c);
        const colors = [...formulaColors];

        // Incluye el color seleccionado si no está en la lista
        if (this.state.colorId && this.pos.models["tint.color"]) {
            const selected = this.pos.models["tint.color"].get(this.state.colorId);
            if (selected && !colors.some((c) => c.id === selected.id)) {
                colors.push(selected);
            }
        }

        const unique = new Map(colors.map((c) => [c.id, c]));
        let list = [...unique.values()];
        if (term) {
            list = list.filter(
                (c) =>
                    (c.name || "").toLowerCase().includes(term) ||
                    (c.code || "").toLowerCase().includes(term)
            );
        }
        return list.sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    }

    get selectedFormula() {
        if (!this.state.colorId) {
            return null;
        }
        return this.formulasForLine.find((f) => f.color_id?.id === this.state.colorId) || null;
    }

    get selectedColor() {
        return this.state.colorId
            ? this.pos.models["tint.color"].get(this.state.colorId)
            : null;
    }

    /** Dosis de colorante de la fórmula seleccionada. */
    get doses() {
        return this.selectedFormula
            ? formulaDoses(this.pos, this.selectedFormula)
            : [];
    }

    get totalPoints() {
        return this.selectedFormula ? this.selectedFormula.total_points : 0;
    }

    get remainingPoints() {
        return this.capacityPoints - this.totalPoints;
    }

    get requiresExtraction() {
        return Boolean(this.baseType && this.baseType.requires_extraction);
    }

    /** Litros a extraer según el porcentaje de la base y volumen del envase. */
    get extractionLiters() {
        if (!this.requiresExtraction || !this.size) {
            return 0;
        }
        return (this.size.volume_liters * (this.baseType.extraction_percentage || 0)) / 100;
    }

    get canConfirm() {
        if (!this.selectedFormula) {
            return false;
        }
        if (this.requiresExtraction && !this.state.extractionDone) {
            return false;
        }
        return true;
    }

    selectColor(colorId) {
        this.state.colorId = colorId;
        this.state.extractionDone = false;
    }

    /** Retorna el color y fórmula seleccionados al llamador y cierra el diálogo. */
    confirm() {
        if (!this.canConfirm) {
            return;
        }
        this.props.getPayload?.({
            colorId: this.state.colorId,
            formulaId: this.selectedFormula.id,
            extractionDone: this.state.extractionDone,
        });
        this.props.close();
    }

    cancel() {
        this.props.close();
    }
}
