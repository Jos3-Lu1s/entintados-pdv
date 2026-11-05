import { registry } from "@web/core/registry";
import { SelectionField, selectionField } from "@web/views/fields/selection/selection_field";

class CustomerPaymentPolicy extends SelectionField {
    get options() {
        return super.options.filter(([value]) => value !== "PPD" ||
            (this.props.record.resModel === "res.partner" && !this.props.record.data.is_sales_contact) ||
            this.props.record.data.payment_credit_authorized);
    }

    get string() {
        // Existing contacts may still carry an invalid policy; keep their label
        // readable while the server prevents saving it without authorization.
        return this.props.record.fields[this.props.name].selection.find(
            ([value]) => value === this.props.record.data[this.props.name])?.[1] || "";
    }
}

registry.category("fields").add("customer_payment_policy", {
    ...selectionField,
    component: CustomerPaymentPolicy,
    fieldDependencies: [
        { name: "is_sales_contact", type: "boolean" },
        { name: "payment_credit_authorized", type: "boolean" },
    ],
});

registry.category("fields").add("sale_customer_payment_policy", {
    ...selectionField,
    component: CustomerPaymentPolicy,
    fieldDependencies: [{ name: "payment_credit_authorized", type: "boolean" }],
});
