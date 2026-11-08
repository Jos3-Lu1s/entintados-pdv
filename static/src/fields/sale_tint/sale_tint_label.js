import { patch } from "@web/core/utils/patch";
import { SaleOrderLineProductField } from "@sale/js/sale_product_field";

// Keep the native base label. The secondary label contains only the frozen color.
patch(SaleOrderLineProductField.prototype, {
    get label() {
        if (this.props.record.data.tint_section_id) {
            return super.label;
        }
        return this.props.record.data.tint_display_name
            ? this.props.record.data.tint_color_description || ""
            : super.label;
    },

    get m2oProps() {
        const props = super.m2oProps;
        const isTinted = this.props.record.data.tint_display_name || this.props.record.data.tint_section_id;
        return isTinted && props.value
            ? { ...props, readonly: true }
            : props;
    },
});
