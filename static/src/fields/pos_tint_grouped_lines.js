import { registry } from "@web/core/registry";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";
import { ListRenderer } from "@web/views/list/list_renderer";
import { _t } from "@web/core/l10n/translation";

export class PosTintListRenderer extends ListRenderer {
    static rowsTemplate = "entintados_pdv.PosTintRows";
    static recordRowTemplate = "entintados_pdv.PosTintRecordRow";

    tintRecords(list) {
        const groups = new Map();
        const regular = [];
        for (const record of list.records) {
            const key = record.data.tint_group_key;
            if (!key) {
                regular.push(record);
                continue;
            }
            if (!groups.has(key)) {
                groups.set(key, []);
            }
            groups.get(key).push(record);
        }
        return [...groups.values()].flatMap((records) => [
            ...records.filter((record) => record.data.tint_line_role === "base"),
            ...records.filter((record) => record.data.tint_line_role !== "base"),
        ]).concat(regular);
    }

    tintHeader(record, list) {
        const records = this.tintRecords(list);
        const index = records.indexOf(record);
        if (record.data.tint_group_key) {
            return index === 0 || records[index - 1].data.tint_group_key !== record.data.tint_group_key
                ? _t("Entintado: %s", record.data.tint_group_name) : "";
        }
        return index > 0 && records[index - 1].data.tint_group_key ? _t("Otros productos") : "";
    }
}

class PosTintGroupedLines extends X2ManyField {
    static components = { ...X2ManyField.components, ListRenderer: PosTintListRenderer };
}

registry.category("fields").add("pos_tint_grouped_lines", {
    ...x2ManyField,
    component: PosTintGroupedLines,
});
