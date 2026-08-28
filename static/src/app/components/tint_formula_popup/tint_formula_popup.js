import { Component, useState, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { formatPoints } from "@entintados_pdv/app/utils/tint_points";
import { formulaDoses } from "@entintados_pdv/app/utils/tint_order";
import { TintCreateColorPopup } from "@entintados_pdv/app/components/tint_create_color_popup/tint_create_color_popup";

/**
 * Diálogo para seleccionar el color y fórmula sobre una base.
 */
export class TintFormulaPopup extends Component {
    static template = "entintados_pdv.TintFormulaPopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        baseTypeId: { optional: true },
        sizeId: { optional: true },
        initialColorId: { optional: true },
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
        this.dialog = useService("dialog");

        this.state = useState({
            search: "",
            colorId: this.props.initialColorId || false,
            extractionDone: false,
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

    async openCreateColor() {
        const payload = await makeAwaitable(this.dialog, TintCreateColorPopup, {
            baseTypeId: this.props.baseTypeId || false,
            sizeId: this.props.sizeId || false,
        });
        if (payload?.colorId) {
            await this.loadFormulasForLine();
            this.selectColor(payload.colorId);
        }
    }

    formatCurrency(amount) {
        if (this.env.utils?.formatCurrency) {
            return this.env.utils.formatCurrency(amount);
        }
        return "$" + Number(amount || 0).toFixed(2);
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
