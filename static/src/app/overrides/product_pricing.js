/** @odoo-module **/

import { ProductTemplateAccounting } from "@point_of_sale/app/models/accounting/product_template_accounting";
import { patch } from "@web/core/utils/patch";
import { roundPrecision } from "@web/core/utils/numbers";
import { _t } from "@web/core/l10n/translation";
import { findPricelistRule } from "@entintados_pdv/app/utils/pricelist_rules";

/**
 * Precio de tarifa con las reglas «por Esquema» y la prioridad de Ventas.
 *
 * Reproduce `ProductTemplateAccounting.getPrice` de point_of_sale (Odoo 19): precio base,
 * tarifa anidada, costo, precio fijo, porcentaje y fórmula con redondeo, recargo y márgenes,
 * y la cantidad acumulada de los productos con lote. Solo cambia la búsqueda de la regla.
 * Si el core cambia ese método, este patch se revisa contra él.
 */
patch(ProductTemplateAccounting.prototype, {
    getPrice(
        pricelist,
        quantity,
        price_extra = 0,
        recurring = false,
        variant = false,
        original_line = false,
        related_lines = []
    ) {
        if (recurring && !pricelist) {
            alert(
                _t(
                    "An error occurred when loading product prices. " +
                        "Make sure all pricelists are available in the POS."
                )
            );
        }

        const product = variant;
        const standardPrice = variant ? variant.standard_price : this.standard_price;
        const basePrice = variant ? variant.lst_price : this.list_price;
        let price = basePrice + (price_extra || 0);

        if (!pricelist) {
            return price;
        }

        if (original_line && original_line.isLotTracked() && product) {
            related_lines.push(
                ...original_line.order_id.lines.filter((line) => line.product_id.id == product.id)
            );
            quantity = related_lines.reduce((sum, line) => sum + line.getQuantity(), 0);
        }

        const rule = findPricelistRule(pricelist, variant || this, quantity);
        if (!rule) {
            return price;
        }
        if (rule.base === "pricelist") {
            if (rule.base_pricelist_id) {
                price = this.getPrice(rule.base_pricelist_id, quantity, 0, true, variant);
            }
        } else if (rule.base === "standard_price") {
            price = standardPrice;
        }

        if (rule.compute_price === "fixed") {
            price = rule.fixed_price;
        } else if (rule.compute_price === "percentage") {
            price = price - price * (rule.percent_price / 100);
        } else {
            const priceLimit = price;
            price -= price * (rule.price_discount / 100);
            if (rule.price_round) {
                price = roundPrecision(price, rule.price_round);
            }
            if (rule.price_surcharge) {
                price += rule.price_surcharge;
            }
            if (rule.price_min_margin) {
                price = Math.max(price, priceLimit + rule.price_min_margin);
            }
            if (rule.price_max_margin) {
                price = Math.min(price, priceLimit + rule.price_max_margin);
            }
        }

        // Sin redondear, como el core: el llamador redondea.
        return price;
    },
});
