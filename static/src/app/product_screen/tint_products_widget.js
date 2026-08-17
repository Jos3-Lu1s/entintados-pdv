
import { useState } from "@odoo/owl";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { runTintFlow } from "@entintados_pdv/app/utils/tint_flow";
import { TintPanel } from "@entintados_pdv/app/product_screen/tint_panel";
import { TintListRow } from "@entintados_pdv/app/components/tint_list_row/tint_list_row";

// Subcomponentes de ProductScreen usados por la plantilla heredada: el panel
// de entintado y la fila de lista reutilizable (para la vista de lista de
// productos).
ProductScreen.components = { ...ProductScreen.components, TintPanel, TintListRow };

/**
 * Atajo de mostrador: color activo + clic en una base.
 *
 * Se engancha en `addProductToOrder`, que es el manejador real del clic en la
 * grilla del POS 19. Recibe un `product.template`, no un `product.product`:
 * la plantilla llama `this.addProductToOrder(product)` sobre los registros de
 * `pos.productToDisplayByCateg`, que son plantillas.
 *
 * Aquí ya no se inyectan colores como productos falsos: los colores viven en
 * `tint.color` y se eligen desde la pantalla del asistente.
 *
 * ## Vista de lista (grid / list)
 *
 * El toggle de vista NO reimplementa el filtrado ni el clic: reutiliza
 * `pos.productsToDisplay` (la MISMA lista que alimenta la cuadrícula, ya
 * filtrada por categoría y búsqueda y ordenada por el core) y el MISMO
 * `addProductToOrder`. La lista se pinta con `TintListRow`, la misma fila que
 * usan las listas de colores y bases del panel de entintado. `viewMode` vive
 * en el estado del componente para que cada apertura de la pantalla arranque
 * en cuadrícula.
 */
patch(ProductScreen.prototype, {
    setup() {
        super.setup();
        // Estado de la pestaña activa y de la vista (cuadrícula/lista). Vive en
        // el componente, no en el store, para que cada apertura de la pantalla
        // arranque en «Productos» y en cuadrícula.
        this.tintUi = useState({ tab: "products", viewMode: "grid" });
    },

    setTintTab(tab) {
        this.tintUi.tab = tab;
    },

    setProductViewMode(mode) {
        this.tintUi.viewMode = mode;
    },

    /**
     * Precio de venta de una plantilla respetando la tarifa de la orden.
     *
     * Delega en `getPrice` del core (mismo cálculo que usa la línea de venta),
     * así que no se duplica la lógica de listas de precios. Sin tarifa cae al
     * precio de lista.
     */
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
