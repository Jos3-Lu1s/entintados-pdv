import { patch } from "@web/core/utils/patch";
import { SaleOrderLineProductField } from "@sale/js/sale_product_field";

// Change only the label on this sale line; keep the actual base product and its stock links.
patch(SaleOrderLineProductField.prototype, {
    get productName() {
        return this.props.record.data.tint_display_name || super.productName;
    },

    get m2oProps() {
        const props = super.m2oProps;
        const title = this.props.record.data.tint_display_name;
        return title && props.value
            ? { ...props, value: { ...props.value, display_name: title }, readonly: true }
            : props;
    },
});
