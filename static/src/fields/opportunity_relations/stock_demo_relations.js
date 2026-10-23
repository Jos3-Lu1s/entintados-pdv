import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { OpportunityRelationBadges } from "./opportunity_relations";

export class StockDemoRelationBadges extends OpportunityRelationBadges {
    get badges() {
        const counts = this.props.record.data[this.props.name] || {};
        return [
            { key: "opportunity", label: _t("Oportunidad"), icon: "fa-star", color: "text-primary bg-primary-subtle" },
            { key: "approval", label: _t("Aprobación"), icon: "fa-user", color: "text-success bg-success-subtle" },
        ].map((badge) => ({ ...badge, count: counts[badge.key] || 0 }))
            .filter((badge) => badge.count > 0);
    }

    async openRelation(key) {
        const action = await this.orm.call("stock.picking", "action_open_demo_relation", [
            [this.props.record.resId], key,
        ]);
        await this.action.doAction(action);
    }
}

registry.category("fields").add("stock_demo_relation_badges", {
    component: StockDemoRelationBadges,
    supportedTypes: ["json"],
});
