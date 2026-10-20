import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { FloatField, floatField } from "@web/views/fields/float/float_field";

export class StockAvailabilityField extends FloatField {
    static template = "entintados_pdv.StockAvailabilityField";

    get hasStock() {
        return this.props.record.data[this.props.name] > 0;
    }

    get stockLabel() {
        return this.hasStock ? _t("Con existencias") : _t("Sin existencias");
    }
}

registry.category("fields").add("entintados_stock_availability", {
    ...floatField,
    component: StockAvailabilityField,
});
