import { useState } from "@odoo/owl";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { runTintFlow } from "@entintados_pdv/app/utils/tint_flow";
import { TintPanel } from "@entintados_pdv/app/screens/product_screen/tint_panel";
import { TintTable } from "@entintados_pdv/app/components/tint_table/tint_table";
import { TintCreateColorPopup } from "@entintados_pdv/app/components/tint_create_color_popup/tint_create_color_popup";

// Registro de subcomponentes TintPanel y TintTable en ProductScreen.
ProductScreen.components = { ...ProductScreen.components, TintPanel, TintTable };

/**
 * Extensión de ProductScreen para soportar vista cuadrícula/lista y
 * el flujo de entintado al seleccionar una base con color activo.
 */
patch(ProductScreen.prototype, {
    setup() {
        super.setup();
        // Estado reactivo compartido para la navegación de entintados y filtros.
        this.tintUi = useState({
            tab: "products",
            viewMode: "grid",
            galleryId: null,
            colorId: null,
            sizeIds: [],
            baseTypeIds: [],
            galleryColorIds: [],
            loadingColors: false,
            formulasVersion: 0,
            loadingFormulas: false,
        });
    },

    setTintTab(tab) {
        this.tintUi.tab = tab;
    },

    setProductViewMode(mode) {
        this.tintUi.viewMode = mode;
    },

    /** Galería seleccionada actualmente. */
    get tintSelectedGallery() {
        return this.tintUi.galleryId
            ? this.pos.models["tint.gallery"]?.get?.(this.tintUi.galleryId)
            : null;
    },

    /** Reinicia la selección de galería y filtros de entintado. */
    changeTintGallery() {
        Object.assign(this.tintUi, {
            galleryId: null,
            colorId: null,
            sizeIds: [],
            baseTypeIds: [],
            galleryColorIds: [],
        });
        this.pos.searchProductWord = "";
    },

    /** Abre el diálogo para crear color desde la pestaña de Entintados */
    async onClickCreateColorFromTab() {
        const payload = await makeAwaitable(this.dialog, TintCreateColorPopup, {
            galleryId: this.tintUi.galleryId || false,
        });
        if (payload?.colorId) {
            if (payload.galleryId && this.tintUi.galleryId !== payload.galleryId) {
                this.tintUi.galleryId = payload.galleryId;
            }
            if (this.tintUi.galleryId) {
                const ids = await this.pos.data.call(
                    "tint.color.formula",
                    "get_color_ids_for_gallery",
                    [this.tintUi.galleryId]
                );
                this.tintUi.galleryColorIds = ids || [];
            }
            this.tintUi.colorId = payload.colorId;
        }
    },

    /** Columnas para la tabla de productos en modo lista. */
    get productListColumns() {
        return [
            { label: "Código", class: "text-nowrap", style: "width: 140px;" },
            { label: "Nombre" },
            { label: "UdM", class: "text-nowrap", style: "width: 100px;" },
            { label: "Precio", class: "text-end text-nowrap", style: "width: 120px;" },
        ];
    },

    /** Precio de venta del producto según la lista de precios activa. */
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
