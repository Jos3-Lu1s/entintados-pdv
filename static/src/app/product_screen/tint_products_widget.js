/** @odoo-module **/

import { useState } from "@odoo/owl";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { runTintFlow } from "@entintados_pdv/app/utils/tint_flow";
import { TintPanel } from "@entintados_pdv/app/product_screen/tint_panel";

// El panel se registra como subcomponente de ProductScreen para poder
// renderizarlo desde la plantilla heredada.
ProductScreen.components = { ...ProductScreen.components, TintPanel };

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
 */
patch(ProductScreen.prototype, {
    setup() {
        super.setup();
        // Estado de la pestaña activa. Vive en el componente, no en el store,
        // para que cada apertura de la pantalla arranque en «Productos».
        this.tintUi = useState({ tab: "products" });
    },

    setTintTab(tab) {
        this.tintUi.tab = tab;
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
