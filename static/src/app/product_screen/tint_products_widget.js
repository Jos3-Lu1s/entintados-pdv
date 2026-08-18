
import { useState } from "@odoo/owl";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { runTintFlow } from "@entintados_pdv/app/utils/tint_flow";
import { TintPanel } from "@entintados_pdv/app/product_screen/tint_panel";
import { TintTable } from "@entintados_pdv/app/components/tint_table/tint_table";

// Registro de subcomponentes TintPanel y TintTable en ProductScreen.
ProductScreen.components = { ...ProductScreen.components, TintPanel, TintTable };

/**
 * Extensión de ProductScreen para soportar vista cuadrícula/lista y
 * el flujo de entintado al seleccionar una base con color activo.
 */
patch(ProductScreen.prototype, {
    setup() {
        super.setup();
        // Estado local de pestaña activa y modo de vista (cuadrícula/lista).
        this.tintUi = useState({ tab: "products", viewMode: "grid" });
    },

    setTintTab(tab) {
        this.tintUi.tab = tab;
    },

    setProductViewMode(mode) {
        this.tintUi.viewMode = mode;
    },

    /** Columnas para la tabla de productos en modo lista. */
    get productListColumns() {
        return [
            { label: "Imagen", class: "o-tint-th-img" },
            { label: "Nombre" },
            { label: "Código", class: "text-nowrap" },
            { label: "UdM", class: "text-nowrap" },
            { label: "Precio", class: "text-end text-nowrap" },
        ];
    },

    /** Precio de venta del producto considerando la tarifa de la orden activa. */
    tintRowPrice(product) {
        const order = this.pos.getOrder();
        return product.getPrice(order?.pricelist_id || false, 1);
    },

    tintRowPriceFormatted(product) {
        const price = this.tintRowPrice(product);
        return this.env.utils?.formatCurrency
            ? this.env.utils.formatCurrency(price)
            : String(price);
    },

    async addProductToOrder(productTmpl) {
        const order = this.pos.getOrder();
        const selectedColor = order?.uiState?.selectedTintColor;
        const baseProduct = productTmpl?.product_variant_ids?.[0];

        if (!selectedColor || productTmpl?.tint_role !== "base" || !baseProduct) {
            return super.addProductToOrder(productTmpl);
        }

        const line = await runTintFlow(this, {
            baseProduct,
            initialColorId: selectedColor.id,
        });
        if (line) {
            this.clearSelectedTintColor();
        }
    },

    clearSelectedTintColor() {
        const order = this.pos.getOrder();
        if (order?.uiState) {
            order.uiState.selectedTintColor = null;
            order.uiState.selectedTintColorId = null;
        }
    },
});
