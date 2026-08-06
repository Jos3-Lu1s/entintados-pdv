/** @odoo-module **/

import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { TintFormulaPopup } from "@entintados_pdv/js/tint_formula_popup";

// 1. Inyectar los colores de tint.color como productos virtuales en pos.models["product.product"]
patch(PosStore.prototype, {
    async afterProcessPosData(data) {
        await super.afterProcessPosData(data);
        this.loadVirtualTintProducts();
    },

    loadVirtualTintProducts() {
        if (!this.models["tint.color"] || !this.models["product.product"]) {
            return;
        }

        const tintColors = this.models["tint.color"].getAll();
        if (!tintColors.length) return;

        // Categoría POS "Carta de Colores"
        let posCategory = this.models["pos.category"]
            ? this.models["pos.category"]
                  .getAll()
                  .find(
                      (c) =>
                          c.name === "Carta de Colores" || c.is_tint_category
                  )
            : null;

        if (!posCategory && this.models["pos.category"]) {
            posCategory = this.models["pos.category"].create({
                id: -9999,
                name: "Carta de Colores",
                is_tint_category: true,
            });
        }

        for (const color of tintColors) {
            const virtualId = -1000 - color.id;
            const existing = this.models["product.product"].get(virtualId);
            const colName =
                color.collection_id?.name ||
                this.models["tint.collection"]?.get(color.collection_id)?.name ||
                "";
            const displayName = color.code
                ? `[${color.code}] ${color.name}`
                : color.name;

            const productData = {
                id: virtualId,
                rawColorId: color.id,
                display_name: displayName,
                name: color.name,
                default_code: color.code || "",
                code: color.code || "",
                html_color: color.html_color || "#9E9E9E",
                collection_name: colName,
                is_tint_color: true,
                isTintColor: true,
                has_image: false,
                image_url: false,
                imageUrl: false,
                get_image_url: () => false,
                lst_price: 0,
                price: 0,
                pos_categ_ids: posCategory ? [posCategory] : [],
                categ_id: posCategory || false,
                tracking: "none",
                available_in_pos: true,
                product_template_variant_value_ids: [],
            };

            if (existing) {
                Object.assign(existing, productData);
            } else {
                this.models["product.product"].create(productData);
            }
        }
    },
});

// 2. Interceptación de clics en productos del ProductScreen para detectar productos virtuales tint.color
patch(ProductScreen.prototype, {
    async onPressProduct(product) {
        if (product && (product.is_tint_color || product.isTintColor)) {
            await this.handleTintColorClick(product);
            return;
        }

        const order = this.pos.getOrder();
        const selectedColor = order?.uiState?.selectedTintColor;

        // Si hay un color activo seleccionado previamente y el cajero hace clic en un producto base:
        const tmpl = product?.product_tmpl_id || product;
        if (selectedColor && tmpl && tmpl.tint_role === "base") {
            if (!tmpl.tint_base_type_id || !tmpl.tint_size_id) {
                this.notification.add(
                    _t("Esta base no tiene tipo o presentación configurados."),
                    { type: "warning" }
                );
                return super.onPressProduct(product);
            }

            // Agregar primero la línea del producto base a la orden
            await super.onPressProduct(product);

            const line = order.getSelectedOrderline();
            const baseTypeId =
                tmpl.tint_base_type_id.id || tmpl.tint_base_type_id;
            const sizeId = tmpl.tint_size_id.id || tmpl.tint_size_id;

            // Limpiar el color activo del estado UI
            order.uiState.selectedTintColor = null;
            order.uiState.selectedTintColorId = null;

            // Abrir TintFormulaPopup con la base y el color preseleccionado
            const payload = await makeAwaitable(this.dialog, TintFormulaPopup, {
                baseTypeId: baseTypeId,
                sizeId: sizeId,
                initialColorId: selectedColor.id,
            });

            if (!payload) return;

            // Aplicar nota e insumos dispensados
            line.setCustomerNote(payload.text);
            if (payload.doses && payload.doses.length > 0) {
                for (const dose of payload.doses) {
                    if (dose.colorantId) {
                        const colorantProduct =
                            this.pos.models["product.product"].get(
                                dose.colorantId
                            );
                        if (colorantProduct) {
                            const price =
                                colorantProduct.price_per_point ||
                                colorantProduct.lst_price ||
                                0;
                            const noteText = _t(
                                "Insumo de entintado para: %s (%s Pts)",
                                line.product_id?.display_name || "",
                                dose.points
                            );
                            await this.pos.addLineToCurrentOrder({
                                product_id: colorantProduct,
                                qty: dose.points,
                                price_unit: price,
                                customer_note: noteText,
                            });
                        }
                    }
                }
            }
            this.notification.add(
                _t("Entintado configurado con el color seleccionado."),
                { type: "success" }
            );
            return;
        }

        return super.onPressProduct(product);
    },

    async handleTintColorClick(colorProduct) {
        const colorId = colorProduct.rawColorId;
        const color = this.pos.models["tint.color"]
            ? this.pos.models["tint.color"].get(colorId)
            : null;
        if (!color) return;

        const order = this.pos.getOrder();
        if (!order) return;

        const line = order.getSelectedOrderline();
        const lineTmpl =
            line?.product_id?.product_tmpl_id || line?.product_id;

        if (line && lineTmpl && lineTmpl.tint_role === "base") {
            // Ya hay una línea base seleccionada en la orden -> Abrir directamente TintFormulaPopup
            const baseTypeId =
                lineTmpl.tint_base_type_id?.id || lineTmpl.tint_base_type_id;
            const sizeId =
                lineTmpl.tint_size_id?.id || lineTmpl.tint_size_id;

            const payload = await makeAwaitable(this.dialog, TintFormulaPopup, {
                baseTypeId: baseTypeId,
                sizeId: sizeId,
                initialColorId: color.id,
            });

            if (!payload) return;

            line.setCustomerNote(payload.text);
            if (payload.doses && payload.doses.length > 0) {
                for (const dose of payload.doses) {
                    if (dose.colorantId) {
                        const colorantProduct =
                            this.pos.models["product.product"].get(
                                dose.colorantId
                            );
                        if (colorantProduct) {
                            const price =
                                colorantProduct.price_per_point ||
                                colorantProduct.lst_price ||
                                0;
                            const noteText = _t(
                                "Insumo de entintado para: %s (%s Pts)",
                                line.product_id?.display_name || "",
                                dose.points
                            );
                            await this.pos.addLineToCurrentOrder({
                                product_id: colorantProduct,
                                qty: dose.points,
                                price_unit: price,
                                customer_note: noteText,
                            });
                        }
                    }
                }
            }
            this.notification.add(
                _t("Entintado y materiales agregados a la orden."),
                { type: "success" }
            );
        } else {
            // No hay base seleccionada -> Guardar color activo e indicar selección de base
            order.uiState.selectedTintColor = color;
            order.uiState.selectedTintColorId = color.id;

            this.notification.add(
                _t(
                    "Color %s seleccionado. Ahora selecciona una base de pintura entintable.",
                    color.code ? `[${color.code}] ${color.name}` : color.name
                ),
                { type: "info" }
            );
        }
    },

    clearSelectedTintColor() {
        const order = this.pos.getOrder();
        if (order && order.uiState) {
            order.uiState.selectedTintColor = null;
            order.uiState.selectedTintColorId = null;
        }
    },
});
