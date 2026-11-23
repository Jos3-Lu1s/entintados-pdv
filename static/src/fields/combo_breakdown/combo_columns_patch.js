import { patch } from "@web/core/utils/patch";
import { SaleOrderLineListRenderer } from "@sale/js/sale_order_line_field/sale_order_line_field";

// La fila del paquete oculta toda columna fuera de `comboColumns`: sin esto no se ven el icono
// que abre el desglose del combo, el origen del acuerdo ni la estrella de promociones.
patch(SaleOrderLineListRenderer.prototype, {
    createViewKey() {
        const key = super.createViewKey(...arguments);
        const nested = this.props.nestedKeyOptionalFieldsData;
        // Apply the new defaults once without retaining the old visible columns.
        if (nested?.model === "sale.order" && nested.field === "order_line") {
            return `${key},sale_order_columns_v2`;
        }
        return key;
    },

    get comboColumns() {
        return [...super.comboColumns, "price_origin", "pricing_rule_origin", "promo_available_info"];
    },
});
