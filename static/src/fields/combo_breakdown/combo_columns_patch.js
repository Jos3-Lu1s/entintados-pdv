import { patch } from "@web/core/utils/patch";
import { SaleOrderLineListRenderer } from "@sale/js/sale_order_line_field/sale_order_line_field";

// La fila del paquete oculta toda columna fuera de `comboColumns`: sin esto no se ve el icono
// que abre el desglose del combo.
patch(SaleOrderLineListRenderer.prototype, {
    get comboColumns() {
        return [...super.comboColumns, "price_origin"];
    },
});
