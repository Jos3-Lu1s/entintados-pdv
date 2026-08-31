import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { patch } from "@web/core/utils/patch";

/**
 * Parche para el componente visual Orderline del POS para evitar la duplicación
 * de precio como anotación secundaria cuando la cantidad de la línea es 1.
 */
patch(Orderline.prototype, {
    get lineScreenValues() {
        const values = super.lineScreenValues;
        const line = this.line;
        if (!line || !values) {
            return values;
        }

        // Si la cantidad es unitaria (1), se oculta la anotación redundante de precio unitario
        // para que solo aparezca el precio en la columna designada del POS.
        if (Math.abs(line.qty) === 1 && !this.props.basic_receipt && this.props.mode === "display") {
            values.displayPriceUnit = false;
        }

        return values;
    },
});
