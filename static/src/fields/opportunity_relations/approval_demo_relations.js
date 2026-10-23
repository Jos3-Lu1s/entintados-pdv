import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { OpportunityRelationBadges } from "./opportunity_relations";

export class ApprovalDemoRelationBadges extends OpportunityRelationBadges {
    get badges() {
        const counts = this.props.record.data[this.props.name] || {};
        return [
            { key: "opportunity", label: _t("Oportunidad"), icon: "fa-star", color: "text-primary bg-primary-subtle" },
            { key: "pickings", label: _t("Salidas de material"), icon: "fa-truck", color: "text-info bg-info-subtle" },
        ].map((badge) => ({ ...badge, count: counts[badge.key] || 0 }))
            .filter((badge) => badge.count > 0);
    }

    async openRelation(key) {
        const action = await this.orm.call("approval.request", "action_open_approval_demo_relation", [
            [this.props.record.resId], key,
        ]);
        await this.action.doAction(action);
    }
}

registry.category("fields").add("approval_demo_relation_badges", {
    component: ApprovalDemoRelationBadges,
    supportedTypes: ["json"],
});
