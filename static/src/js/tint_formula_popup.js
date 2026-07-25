import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class TintFormulaPopup extends Component {
    static template = "entintados_pdv.TintFormulaPopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        base: { type: String, optional: true },
        getPayload: { type: Function, optional: true },
        close: Function,
    };
    static defaultProps = {
        title: _t("Configurar entintado"),
        base: "",
    };

    setup() {
        this.state = useState({
            base: this.props.base || "",
            colorants: [],
            newName: "",
            newPoints: "",
        });
    }

    addColorant() {
        const name = this.state.newName.trim();
        const points = parseFloat(this.state.newPoints);
        if (!name || !(points > 0)) {
            return;
        }
        this.state.colorants.push({ name, points });
        this.state.newName = "";
        this.state.newPoints = "";
    }

    removeColorant(index) {
        this.state.colorants.splice(index, 1);
    }

    get formulaText() {
        const base = this.state.base
            ? _t("Base %s", this.state.base)
            : _t("Base sin especificar");
        const parts = this.state.colorants.map((c) => `${c.name}: ${c.points} pts`);
        return [base, ...parts].join(" | ");
    }

    confirm() {
        this.props.getPayload?.({
            base: this.state.base,
            colorants: this.state.colorants,
            text: this.formulaText,
        });
        this.props.close();
    }

    cancel() {
        this.props.close();
    }
}
