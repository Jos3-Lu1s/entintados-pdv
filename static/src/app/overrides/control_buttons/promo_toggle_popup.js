/** @odoo-module */
import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class PromoTogglePopup extends Component {
    static template = "entintados_pdv.PromoTogglePopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        promoReward: { type: Object, optional: true },
        partner: { type: [Object, Boolean], optional: true },
        currentMode: { type: String, optional: true },
        onSelect: Function,
        close: Function,
    };
    static defaultProps = {
        title: _t("Alternar Promoción vs Acuerdos Comerciales"),
        promoReward: null,
        partner: null,
        currentMode: "commercial_agreements",
    };

    get promoName() {
        const reward = this.props.promoReward?.reward || this.props.promoReward;
        const models = this.env?.services?.pos?.models;
        const program =
            reward?.program_id && typeof reward.program_id === "object"
                ? reward.program_id
                : (models?.["loyalty.program"]?.get?.(reward?.program_id) || null);
        return program?.name || reward?.description || _t("Promoción Vigente");
    }

    get promoDiscount() {
        const reward = this.props.promoReward?.reward || this.props.promoReward;
        return reward?.discount ? Math.min(reward.discount, 100) : 0;
    }

    get partnerName() {
        return this.props.partner?.name || this.props.partner?.display_name || _t("Cliente");
    }

    get isCurrentPromo() {
        return this.props.currentMode === "promo";
    }

    get isCurrentAgreements() {
        return this.props.currentMode === "commercial_agreements";
    }

    selectMode(mode) {
        this.props.onSelect(mode);
        this.props.close();
    }
}
