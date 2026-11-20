import { registry } from "@web/core/registry";
import { Group } from "@web/model/relational_model/group";
import { DynamicGroupList } from "@web/model/relational_model/dynamic_group_list";
import { RelationalModel } from "@web/model/relational_model/relational_model";
import { listView } from "@web/views/list/list_view";
import { ListRenderer } from "@web/views/list/list_renderer";

class CashCutRenderer extends ListRenderer {
    getGroupLevel(group) {
        if (this.props.list.groupBy[0] === "method") {
            if (group.groupByField.name === "method") {
                return 0;
            }
            if (group.groupByField.name === "subtype") {
                return 1;
            }
        }
        return super.getGroupLevel(group);
    }
}

class CashCutGroup extends Group {
    setup(config, data) {
        if (config.groupByFieldName === "method" && !["Tarjeta", "Tarjeta de crédito"].includes(data.value)) {
            // Each group needs its own array: Odoo shares the next-level grouping.
            config.list.groupBy = config.list.groupBy.filter((field) => field !== "subtype");
            if (!config.list.groupBy.length && !data.records) {
                data = { ...data, records: [], length: data.count };
                config.isFolded = true;
            }
        }
        super.setup(config, data);
    }
}

class CashCutGroupList extends DynamicGroupList {
    _setData(data) {
        if (this.config.groupBy[0] === "method") {
            const order = ["Cheque", "Efectivo", "Transferencia", "Tarjeta", "Tarjeta de crédito"];
            const rank = (group) => {
                const index = order.indexOf(group.value);
                return index < 0 ? order.length : index;
            };
            data = { ...data, groups: [...data.groups].sort((a, b) => rank(a) - rank(b)) };
        }
        super._setData(data);
    }
}

class CashCutModel extends RelationalModel {
    static Group = CashCutGroup;
    static DynamicGroupList = CashCutGroupList;

    async _postprocessReadGroup(config, response) {
        if (config.groupBy[0] === "method" && config.groupBy.includes("subtype")) {
            const groups = response.groups.map((group) => {
                if (["Tarjeta", "Tarjeta de crédito"].includes(group.method)) {
                    return group;
                }
                const { __groups, ...foldedGroup } = group;
                return foldedGroup;
            });
            response = { ...response, groups };
        }
        return super._postprocessReadGroup(config, response);
    }
}

registry.category("views").add("branch_cash_cut_groups", {
    ...listView,
    Model: CashCutModel,
    Renderer: CashCutRenderer,
});
