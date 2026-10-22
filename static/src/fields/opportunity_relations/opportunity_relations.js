import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class OpportunityRelationBadges extends Component {
    static template = "entintados_pdv.OpportunityRelationBadges";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
    }

    get badges() {
        const counts = this.props.record.data[this.props.name] || {};
        return [
            { key: "approvals", label: _t("Aprobaciones"), icon: "fa-check-square-o", color: "text-success bg-success-subtle" },
            { key: "quotations", label: _t("Cotizaciones"), icon: "fa-file-text-o", color: "text-primary bg-primary-subtle" },
            { key: "pickings", label: _t("Salidas de material"), icon: "fa-truck", color: "text-info bg-info-subtle" },
        ].map((badge) => ({ ...badge, count: counts[badge.key] || 0 }))
            .filter((badge) => badge.count > 0);
    }

    async openRelation(key) {
        const action = await this.orm.call("crm.lead", "action_open_opportunity_relation", [
            [this.props.record.resId], key,
        ]);
        await this.action.doAction(action);
    }
}

registry.category("fields").add("opportunity_relation_badges", {
    component: OpportunityRelationBadges,
    supportedTypes: ["json"],
});
