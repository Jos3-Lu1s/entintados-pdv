import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { formatPoints } from "@entintados_pdv/app/utils/tint_points";

// Se reexporta para no romper importaciones existentes. La implementación
// vive ahora en `app/utils/tint_points.js`.
export { formatPoints };

/**
 * Popup de entintado en caja.
 *
 * La base y la presentación las fija el producto de la línea seleccionada
 * (se reciben por props). El cajero solo elige el color de la carta; el
 * sistema resuelve la fórmula correspondiente, muestra las dosis y, si la
 * base lo requiere, exige el acuse de extracción previa.
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
        this.state = useState({
            activeTab: "tint", // "tint" | "create_color"
            search: "",
            colorId: this.props.initialColorId || false,
            extractionDone: false,

            // Formulario para crear nuevo color (tint.color)
            newColorName: "",
            newColorCode: "",
            newColorHtml: "#ffffff",
            newColorCollectionId: "",
            newColorNotes: "",

            // Selección de Base y Presentación para la fórmula inicial
            newBaseTypeId: this.props.baseTypeId ? String(this.props.baseTypeId) : "",
            newSizeId: this.props.sizeId ? String(this.props.sizeId) : "",

            // Fórmula inicial (tint.color.formula y tint.color.formula.line)
            selectedColorantId: "",
            newColorantPoints: 1,
            newFormulaLines: [],

            isCreatingColor: false,
            createColorError: "",
            createColorSuccess: "",
        });
    }

    setTab(tab) {
        this.state.activeTab = tab;
        this.state.createColorError = "";
        this.state.createColorSuccess = "";
    }

    onColorInput(ev) {
        this.state.newColorHtml = ev.target.value;
    }

    get collections() {
        if (!this.pos.models["tint.collection"]) {
            return [];
        }
        return [...this.pos.models["tint.collection"].getAll()]
            .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
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

    addColorantLine() {
        const colorantId = parseInt(this.state.selectedColorantId);
        const points = parseInt(this.state.newColorantPoints);
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
            existing.points += points;
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

        const baseTypeId = parseInt(this.state.newBaseTypeId);
        const sizeId = parseInt(this.state.newSizeId);

        if (this.state.newFormulaLines.length > 0) {
            if (!baseTypeId || !sizeId) {
                this.state.createColorError = _t("Para registrar la fórmula debes seleccionar el Tipo de Base y la Presentación.");
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
                html_color: this.state.newColorHtml || false,
            };
            if (this.state.newColorCode.trim()) {
                vals.code = this.state.newColorCode.trim();
            }
            if (this.state.newColorCollectionId) {
                vals.collection_id = parseInt(this.state.newColorCollectionId);
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

            // 2. Si hay líneas de dosis y se seleccionó base + presentación, crear fórmula
            if (colorId && this.state.newFormulaLines.length > 0 && baseTypeId && sizeId) {
                const formulaVals = {
                    color_id: colorId,
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
            this.state.newColorHtml = "#ffffff";
            this.state.newColorCollectionId = "";
            this.state.newColorNotes = "";
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

    /** Capacidad del envase para esta base y presentación (puntos). */
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

    /** Fórmulas cuya base y presentación coinciden con la línea. */
    get formulasForLine() {
        return this.pos.models["tint.color.formula"]
            .getAll()
            .filter(
                (f) =>
                    f.base_type_id?.id === this.props.baseTypeId &&
                    f.size_id?.id === this.props.sizeId
            );
    }

    /** Colores capturables sobre esta base/presentación, filtrados por búsqueda. */
    get availableColors() {
        const term = this.state.search.trim().toLowerCase();
        const formulaColors = this.formulasForLine.map((f) => f.color_id).filter((c) => c);
        const colors = [...formulaColors];

        // Incluir el color seleccionado actualmente (ej. recién creado)
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

    /** Dosis de la fórmula, ordenadas por secuencia. */
    get doses() {
        const formula = this.selectedFormula;
        if (!formula) {
            return [];
        }
        return [...(formula.line_ids || [])]
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .map((l) => ({
                id: l.id,
                colorantId: l.colorant_id?.id,
                name: l.colorant_id?.display_name || l.colorant_id?.name || _t("(colorante)"),
                points: l.points,
            }));
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

    /** Litros a extraer antes de entintar, derivados de la base y la presentación. */
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

    /** Resumen legible que, por ahora, se guarda como nota de la línea. */
    get summaryText() {
        const color = this.selectedColor;
        const parts = [
            `${color?.code ? "[" + color.code + "] " : ""}${color?.name || ""}`,
            `${this.baseType?.code || ""} · ${this.size?.name || ""}`,
            ...this.doses.map((d) => `${d.name}: ${this.formatPoints(d.points)}`),
            `Total: ${this.formatPoints(this.totalPoints)}`,
        ];
        if (this.requiresExtraction) {
            parts.push(_t("Extraer %s L antes de entintar", this.extractionLiters.toFixed(1)));
        }
        return parts.join(" | ");
    }

    confirm() {
        if (!this.canConfirm) {
            return;
        }
        this.props.getPayload?.({
            colorId: this.state.colorId,
            formulaId: this.selectedFormula.id,
            totalPoints: this.totalPoints,
            extractionDone: this.state.extractionDone,
            text: this.summaryText,
            doses: this.doses,
        });
        this.props.close();
    }

    cancel() {
        this.props.close();
    }
}
