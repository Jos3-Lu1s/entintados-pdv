import { patch } from "@web/core/utils/patch";
import { SaleOrderLineListRenderer } from "@sale/js/sale_order_line_field/sale_order_line_field";

// La fila del paquete oculta toda columna fuera de `comboColumns`: sin esto no se ven el icono
// que abre el desglose del combo, el origen del acuerdo ni la estrella de promociones.
patch(SaleOrderLineListRenderer.prototype, {
    get comboColumns() {
        return [...super.comboColumns, "price_origin", "pricing_rule_origin", "promo_available_info"];
    },
});
