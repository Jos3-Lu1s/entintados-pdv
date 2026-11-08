import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { FloatField, floatField } from "@web/views/fields/float/float_field";
import { formatFloat } from "@web/views/fields/formatters";

function componentHeaderId(record) {
    const value = record.data.tint_section_id;
    return Array.isArray(value) ? value[0] : value?.id || value || false;
}

/**
 * Number of containers of a tint, edited on its header. Only whole numbers above zero are
 * accepted; a valid change rescales the base (one per container) and each colorant
 * (points per container) right away, keeping their frozen prices.
 */
export class TintContainerQtyField extends FloatField {
    setup() {
        super.setup();
        this.notification = useService("notification");
        let lastValue;
        useRecordObserver(async (record) => {
            const value = record.data[this.props.name];
            if (lastValue === undefined) {
                lastValue = value;
                return;
            }
            if (value === lastValue) {
                return;
            }
            lastValue = value;
            await this.syncComponents(record, value);
        });
    }

    // Same precision as the component quantities, so the header lines up with them.
    get formattedValue() {
        return formatFloat(this.value, {
            digits: this.props.digits,
            field: this.props.record.fields.product_uom_qty,
        });
    }

    parse(value) {
        const quantity = super.parse(value);
        if (!Number.isFinite(quantity) || quantity <= 0 || !Number.isInteger(quantity)) {
            this.notification.add(_t("Indica un número entero de envases mayor que cero."), {
                type: "danger",
            });
            return this.value;
        }
        return quantity;
    }

    async syncComponents(header, containers) {
        if (!header.resId || !Number.isInteger(containers) || containers <= 0) {
            return;
        }
        const snapshot = header.data.tint_snapshot || {};
        const points = {};
        for (const dose of snapshot.doses || []) {
            points[dose.product_id] = (points[dose.product_id] || 0) + dose.points;
        }
        const siblings = header._parentRecord?.data.order_line?.records || [];
        for (const component of siblings) {
            if (componentHeaderId(component) !== header.resId) {
                continue;
            }
            const productId = component.data.product_id?.id;
            const quantity =
                productId === snapshot.base_id ? containers : (points[productId] || 0) * containers;
            const changes = {};
            if (component.data.product_uom_qty !== quantity) {
                changes.product_uom_qty = quantity;
            }
            if (component.data.tint_container_qty !== containers) {
                changes.tint_container_qty = containers;
            }
            if (Object.keys(changes).length) {
                await component.update(changes);
            }
        }
    }
}

export const tintContainerQtyField = {
    ...floatField,
    component: TintContainerQtyField,
};

registry.category("fields").add("tint_container_qty", tintContainerQtyField);
