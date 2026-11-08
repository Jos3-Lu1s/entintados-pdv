import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";
import { SaleOrderLineListRenderer } from "@sale/js/sale_order_line_field/sale_order_line_field";

function getPreviousSection(list, record) {
    if (!list || !list.records) return null;
    const index = list.records.findIndex((r) => r.id === record.id);
    if (index <= 0) return null;
    for (let i = index - 1; i >= 0; i--) {
        if (list.records[i].data.display_type === "line_section") {
            return list.records[i];
        }
    }
    return null;
}

patch(SaleOrderLineListRenderer.prototype, {
    get comboColumns() {
        return [...super.comboColumns, "price_origin"];
    },

    shouldCollapse(record, fieldName, checkSection = false) {
        if (!checkSection && !this.isSection(record)) {
            const parentSection = getPreviousSection(this.props.list, record);
            if (parentSection?.data?.tint_snapshot) {
                const sectionId = Array.isArray(record.data.tint_section_id)
                    ? record.data.tint_section_id[0]
                    : record.data.tint_section_id?.id || record.data.tint_section_id;
                if (!sectionId || sectionId !== parentSection.resId) {
                    return false;
                }
            }
        }
        return super.shouldCollapse(record, fieldName, checkSection);
    },

    getRowClass(record) {
        let classNames = super.getRowClass(record);
        if (record.data.tint_section_id || (record.data.display_type === "line_section" && record.data.tint_snapshot)) {
            classNames = `${classNames.replace(/\bo_row_draggable\b/g, "").replace(/\bo_tint_no_handle\b/g, "").trim()} o_tint_no_handle`;
        } else {
            const parentSection = getPreviousSection(this.props.list, record);
            if (parentSection?.data?.tint_snapshot) {
                classNames = classNames.replace(/\btext-muted\b/g, "").trim();
            }
        }
        return classNames;
    },

    // Same rule as combo items: a read-only product cell renders as a link, which gives
    // tint components the combo item color and keeps them apart from regular lines.
    isCellReadonly(column, record) {
        return super.isCellReadonly(column, record) || (
            Boolean(record.data.tint_section_id)
                && !["tax_ids", "qty_delivered"].includes(column.name)
        );
    },

    displayDeleteIcon(record) {
        if (record.data.tint_section_id) {
            return false;
        }
        return super.displayDeleteIcon(record);
    },

    getFormattedValue(column, record) {
        if (this.isSection(record) && record.data.tint_snapshot && this.props.aggregatedFields?.includes(column.name)) {
            const sectionRecords = this.props.list.records.filter((r) => {
                if (this.isSection(r)) return false;
                const secId = Array.isArray(r.data.tint_section_id)
                    ? r.data.tint_section_id[0]
                    : r.data.tint_section_id?.id || r.data.tint_section_id;
                return secId && secId === record.resId;
            });
            const total = sectionRecords.reduce((total, r) => total + (r.data[column.name] || 0), 0);
            const formatter = registry.category("formatters").get(column.fieldType, (val) => val);
            return formatter(total, {
                ...formatter.extractOptions?.(column),
                data: record.data,
                field: record.fields[column.name],
            });
        }
        return super.getFormattedValue(column, record);
    },
});
