import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { patch } from "@web/core/utils/patch";

/**
 * Parche para PosOrderline para asegurar que las líneas padre con líneas de combo
 * (como las bases entintadas con colorantes) incluyan su propio precio consolidado
 * en el cálculo del precio a mostrar.
 */
patch(PosOrderline.prototype, {
    get displayPrice() {
        const selfPrice =
            this.config.iface_tax_included === "total"
                ? this.priceIncl
                : this.priceExcl;

        if (!this.combo_line_ids?.length) {
            return selfPrice;
        }

        const comboSum = this.combo_line_ids.reduce((total, cl) => {
            const price =
                this.config.iface_tax_included === "total" ? cl.priceIncl : cl.priceExcl;
            return total + price;
        }, 0);

        return selfPrice + comboSum;
    },

    get displayPriceNoDiscount() {
        const selfPrice =
            this.config.iface_tax_included === "total"
                ? this.priceInclNoDiscount
                : this.priceExclNoDiscount;

        if (!this.combo_line_ids?.length) {
            return selfPrice;
        }

        const comboSum = this.combo_line_ids.reduce((total, cl) => {
            const price =
                this.config.iface_tax_included === "total"
                    ? cl.priceInclNoDiscount
                    : cl.priceExclNoDiscount;
            return total + price;
        }, 0);

        return selfPrice + comboSum;
    },
});
