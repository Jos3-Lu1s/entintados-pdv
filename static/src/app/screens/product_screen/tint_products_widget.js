import { useEffect, useState } from "@odoo/owl";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { runTintFlow } from "@entintados_pdv/app/utils/tint_flow";
import { TintPanel } from "@entintados_pdv/app/screens/product_screen/tint_panel";
import { TintTable } from "@entintados_pdv/app/components/tint_table/tint_table";
import { TintCreateColorPopup } from "@entintados_pdv/app/components/tint_create_color_popup/tint_create_color_popup";
import { requiresPosStock } from "@entintados_pdv/app/utils/pos_stock";
import { resolvePresentationRange } from "@entintados_pdv/app/utils/tint_order";

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
            viewMode: "list",
            galleryId: null,
            colorId: null,
            sizeIds: [],
            baseTypeIds: [],
            galleryColorIds: [],
            loadingColors: false,
            formulasVersion: 0,
            loadingFormulas: false,
        });
        // La cámara se dibuja dentro de la zona de productos, que la pestaña Entintados oculta.
        useEffect(
            (scanning) => {
                if (scanning) {
                    this.tintUi.tab = "products";
                }
            },
            () => [this.pos.scanning]
        );
    },

    get isTintEnabled() {
        return !this.pos.config.module_pos_restaurant;
    },

    setTintTab(tab) {
        if (!this.pos.requireCustomer()) {
            return;
        }
        if (tab === "tint") {
            this.pos.scanning = false;
        }
        this.tintUi.tab = tab;
    },

    setProductViewMode(mode) {
        this.tintUi.viewMode = mode;
    },

    /** Galería seleccionada actualmente. */
    get tintSelectedGallery() {
        return this.isTintEnabled && this.tintUi.galleryId
            ? this.pos.models["tint.gallery"]?.get?.(this.tintUi.galleryId)
            : null;
    },

    /** Reinicia la selección de galería y filtros de entintado. */
    changeTintGallery() {
        if (!this.isTintEnabled) {
            return;
        }
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
        if (!this.pos.requireCustomer()) {
            return;
        }
        const payload = await makeAwaitable(this.dialog, TintCreateColorPopup);
        if (payload?.colorId) {
            if (payload.galleryId) {
                this.tintUi.galleryId = payload.galleryId;
                const ids = await this.pos.data.call(
                    "tint.color.formula",
                    "get_color_ids_for_gallery",
                    [this.tintUi.galleryId]
                );
                this.tintUi.galleryColorIds = ids || [];
                if (!this.tintUi.galleryColorIds.includes(payload.colorId)) {
                    this.tintUi.galleryColorIds.push(payload.colorId);
                }
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
            { label: "Existencias", class: "text-end text-nowrap", style: "width: 120px;" },
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

    tintRowPriceRange(product) {
        const range = resolvePresentationRange(this.pos, product);
        const price = this.tintRowPrice(product);
        const hasMinimum = range.priceMin > 0 && price <= range.priceMin;
        const hasMaximum = !hasMinimum && range.priceMax > 0 && price >= range.priceMax;
        const displayPrice = hasMinimum ? range.priceMin : hasMaximum ? range.priceMax : price;
        const format = (amount) => this.env.utils?.formatCurrency
            ? this.env.utils.formatCurrency(amount) : String(amount);
        return {
            minimum: format(range.priceMin),
            maximum: format(range.priceMax),
            hasMinimum,
            hasMaximum,
            displayPrice: format(displayPrice),
            unitPrice: displayPrice,
            priceClass: hasMinimum ? 'text-warning-emphasis' : hasMaximum ? 'text-info-emphasis' : 'text-dark',
        };
    },

    getAvailableQty(product) {
        const stock = Number(product.pos_stock_qty || 0);
        const inOrder = Number(this.state.quantityByProductTmplId?.[product.id] || 0);
        return Number((stock - inOrder).toFixed(3));
    },

    isProductOutOfStock(product) {
        return (
            requiresPosStock(product) &&
            this.getAvailableQty(product) <= 0 &&
            !this.pos.getOrder()?.preset_id?.is_return
        );
    },

    async addProductToOrder(productTmpl) {
        if (!this.pos.requireCustomer()) {
            return;
        }
        if (!this.isTintEnabled) {
            return super.addProductToOrder(productTmpl);
        }

        const order = this.pos.getOrder();
        const selectedColor = order?.uiState?.selectedTintColor;
        const baseProduct = productTmpl?.product_variant_ids?.[0];

        if (!selectedColor || productTmpl?.tint_role !== "base" || !baseProduct) {
            if (this.tintUi.tab === 'products' && this.tintUi.viewMode === 'list'
                && !order?.preset_id?.is_return) {
                const range = this.tintRowPriceRange(productTmpl);
                if (range.hasMinimum || range.hasMaximum) {
                    const options = {};
                    if (this.searchWord && productTmpl.isConfigurable()) {
                        const variants = productTmpl.product_variant_ids.filter((variant) =>
                            (variant.barcode && variant.barcode.includes(this.searchWord)) ||
                            (variant.default_code && variant.default_code.includes(this.searchWord)));
                        if (variants.length === 1) {
                            options.presetVariant = variants[0];
                        }
                    }
                    const line = await this.pos.addLineToCurrentOrder({
                        product_tmpl_id: productTmpl,
                        price_unit: range.unitPrice,
                        price_type: 'manual',
                        price_origin: 'manual',
                        price_origin_label: range.hasMinimum
                            ? 'Precio mínimo de la línea' : 'Precio máximo de la línea',
                    }, options);
                    if (line) {
                        this.showOptionalProductPopupIfNeeded(productTmpl);
                    }
                    return line;
                }
            }
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
      toggleStockFilter(mode) {
        this.tintUi.stockFilter = this.tintUi.stockFilter === mode ? null : mode;
    },

    /** Con existencias: cantidad > 0. Sin existencias: producto controlado con cantidad <= 0. */
    matchesStockFilter(product, mode) {
        const qty = Number(product.pos_stock_qty || 0);
        return mode === "in_stock" ? qty > 0 : requiresPosStock(product) && qty <= 0;
    },

    /** Lista de la tabla: parte de lo que ya devuelve pos.productsToDisplay y le aplica el filtro. */
    getListProducts() {
        const products = this.pos.productsToDisplay;
        const mode = this.tintUi.stockFilter;
        return mode ? products.filter((p) => this.matchesStockFilter(p, mode)) : products;
    },
});
