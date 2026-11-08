import { patch } from "@web/core/utils/patch";
import { SaleOrderLineListRenderer } from "@sale/js/sale_order_line_field/sale_order_line_field";
import {
    getParentSectionRecord,
    getSectionRecords,
} from "@account/components/section_and_note_fields_backend/section_and_note_fields_backend";

function getComboRecords(listRecords, record) {
    const comboRecords = [];
    if (record.data.product_type === "combo") {
        comboRecords.push(record);
        let index = listRecords.indexOf(record) + 1;
        while (index < listRecords.length) {
            const r = listRecords[index];
            if (
                !r.data.combo_item_id?.id ||
                (r.data.linked_line_id?.id !== record.resId &&
                    r.data.linked_virtual_id !== record.data.virtual_id)
            ) {
                break;
            }
            comboRecords.push(r);
            index++;
        }
    } else if (record.data.combo_item_id?.id) {
        let index = listRecords.indexOf(record);
        while (index >= 0) {
            const r = listRecords[index];
            comboRecords.unshift(r);
            if (
                r.data.product_type === "combo" &&
                (r.resId === record.data.linked_line_id?.id ||
                    r.data.virtual_id === record.data.linked_virtual_id)
            ) {
                break;
            }
            index--;
        }
    }
    return comboRecords.length ? comboRecords : [record];
}

function getParentTintSection(listRecords, record) {
    if (!record || !record.data) return null;
    const parentId = Array.isArray(record.data.tint_section_id)
        ? record.data.tint_section_id[0]
        : record.data.tint_section_id?.id || record.data.tint_section_id;
    if (!parentId) return null;

    let index = listRecords.indexOf(record) - 1;
    while (index >= 0) {
        const r = listRecords[index];
        if (
            r.data.display_type === "line_section" &&
            (r.resId === parentId || r.id === parentId)
        ) {
            return r;
        }
        index--;
    }
    return null;
}

function getTintSectionRecords(listRecords, sectionRecord) {
    const records = [sectionRecord];
    const sectionResId = sectionRecord.resId;
    let index = listRecords.indexOf(sectionRecord) + 1;
    while (index < listRecords.length) {
        const r = listRecords[index];
        if (r.data.display_type) {
            break;
        }
        const parentId = Array.isArray(r.data.tint_section_id)
            ? r.data.tint_section_id[0]
            : r.data.tint_section_id?.id || r.data.tint_section_id;
        if ((sectionResId && parentId === sectionResId) || (!sectionResId && parentId)) {
            records.push(r);
        } else {
            break;
        }
        index++;
    }
    return records;
}

patch(SaleOrderLineListRenderer.prototype, {
    isTintSection(record) {
        return Boolean(
            record &&
            record.data &&
            (record.data.is_tint_section ||
                (record.data.display_type === "line_section" && record.data.tint_snapshot))
        );
    },

    getTintSectionRecords(record) {
        return getTintSectionRecords(this.props.list.records, record);
    },

    canMoveTintUp(record) {
        return Boolean(this.getPreviousTintRecords(record));
    },

    canMoveTintDown(record) {
        return Boolean(this.getNextTintRecords(record));
    },

    getPreviousTintRecords(record) {
        const { records } = this.props.list;
        const index = records.indexOf(record);
        if (index <= 0) return false;

        const prevRecord = records[index - 1];
        if (prevRecord.data.tint_section_id) {
            const parentTint = getParentTintSection(records, prevRecord);
            if (parentTint) {
                return getTintSectionRecords(records, parentTint);
            }
        }
        if (this.isTintSection(prevRecord)) {
            return getTintSectionRecords(records, prevRecord);
        }
        if (prevRecord.data.combo_item_id?.id || prevRecord.data.product_type === "combo") {
            return getComboRecords(records, prevRecord);
        }
        if (prevRecord.data.display_type === "line_section") {
            return getSectionRecords(this.props.list, prevRecord);
        }
        const parentSection = getParentSectionRecord(this.props.list, prevRecord);
        if (parentSection && parentSection.data.display_type === "line_section" && !this.isTintSection(parentSection)) {
            return getSectionRecords(this.props.list, parentSection);
        }
        return [prevRecord];
    },

    getNextTintRecords(record) {
        const { records } = this.props.list;
        const tintRecords = this.getTintSectionRecords(record);
        const nextIndex = records.indexOf(record) + tintRecords.length;
        if (nextIndex >= records.length) return false;

        const nextRecord = records[nextIndex];
        if (this.isTintSection(nextRecord)) {
            return getTintSectionRecords(records, nextRecord);
        }
        if (nextRecord.data.product_type === "combo") {
            return getComboRecords(records, nextRecord);
        }
        if (nextRecord.data.display_type === "line_section") {
            return getSectionRecords(this.props.list, nextRecord);
        }
        return [nextRecord];
    },

    getTintSwapPairs(record, direction) {
        const tintRecords = this.getTintSectionRecords(record);
        if (direction === "up") {
            const previousRecords = this.getPreviousTintRecords(record);
            if (!previousRecords) return { movingRecords: [], targetRecords: [] };
            return {
                movingRecords: previousRecords,
                targetRecords: tintRecords,
            };
        }
        if (direction === "down") {
            const nextRecords = this.getNextTintRecords(record);
            if (!nextRecords) return { movingRecords: [], targetRecords: [] };
            return {
                movingRecords: tintRecords,
                targetRecords: nextRecords,
            };
        }
        return { movingRecords: [], targetRecords: [] };
    },

    async moveTintSection(record, direction) {
        const canProceed = await this.props.list.leaveEditMode({ canAbandon: false });
        if (!canProceed) return;

        const { movingRecords, targetRecords } = this.getTintSwapPairs(record, direction);
        if (!movingRecords.length || !targetRecords.length) return;
        return this.swapSections(movingRecords, targetRecords);
    },

    moveTintUp(record) {
        return this.moveTintSection(record, "up");
    },

    moveTintDown(record) {
        return this.moveTintSection(record, "down");
    },
});
