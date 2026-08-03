import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

const POINTS_PER_OUNCE = 48;

/** Notación mixta de la operación: 456 -> "9Y 24", 96 -> "2Y", 24 -> "24 Pts." */
export function formatPoints(total) {
    total = Math.round(total || 0);
    const ounces = Math.trunc(total / POINTS_PER_OUNCE);
    const rest = total % POINTS_PER_OUNCE;
    if (ounces && rest) {
        return `${ounces}Y ${rest}`;
    }
    if (ounces) {
        return `${ounces}Y`;
    }
    return `${rest} Pts.`;
}

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
        getPayload: { type: Function, optional: true },
        close: Function,
    };
    static defaultProps = {
        title: _t("Configurar entintado"),
        baseTypeId: false,
        sizeId: false,
    };

    setup() {
        this.pos = useService("pos");
        this.state = useState({
            search: "",
            colorId: false,
            extractionDone: false,
        });
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
        const colors = this.formulasForLine.map((f) => f.color_id).filter((c) => c);
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
                name: l.colorant_id?.display_name || _t("(colorante)"),
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
        });
        this.props.close();
    }

    cancel() {
        this.props.close();
    }
}
