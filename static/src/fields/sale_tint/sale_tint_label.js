import { patch } from "@web/core/utils/patch";
import { SaleOrderLineProductField } from "@sale/js/sale_product_field";

// Keep the native base label. The secondary label contains only the frozen color.
patch(SaleOrderLineProductField.prototype, {
    get label() {
        return this.props.record.data.tint_display_name
            ? this.props.record.data.tint_color_description || ""
            : super.label;
    },

    get m2oProps() {
        const props = super.m2oProps;
        return this.props.record.data.tint_display_name && props.value
            ? { ...props, readonly: true }
            : props;
    },
});
