import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

patch(PosStore.prototype, {
    editPartnerContext(partner) {
        return {
            ...super.editPartnerContext(partner),
            default_is_customer: true,
        };
    },

    handlePriceUnit(line, options = {}) {
        if (line?.price_type === "manual" || line?.manual_price) {
            return;
        }

        const order = line?.order_id || this.getOrder?.() || this.currentOrder;
        let pricelist =
            options?.pricelist ||
            line?.pricelist ||
            order?.pricelist ||
            order?.pricelist_id;

        if (!pricelist || typeof pricelist.getPrice !== "function") {
            if (this.config?.pricelist_id && typeof this.config.pricelist_id.getPrice === "function") {
                pricelist = this.config.pricelist_id;
            } else if (this.default_pricelist && typeof this.default_pricelist.getPrice === "function") {
                pricelist = this.default_pricelist;
            } else if (this.pricelist && typeof this.pricelist.getPrice === "function") {
                pricelist = this.pricelist;
            } else if (this.models?.["product.pricelist"]) {
                const allPricelists = this.models["product.pricelist"].getAll?.() || [];
                pricelist = allPricelists.find((p) => typeof p?.getPrice === "function") || allPricelists[0];
            }
        }

        if (line && !line.order_id && order) {
            line.order_id = order;
        }

        if (pricelist && typeof pricelist.getPrice === "function") {
            const updatedOptions = {
                ...options,
                pricelist,
            };
            try {
                return super.handlePriceUnit(line, updatedOptions);
            } catch (err) {
                console.warn("[ENTINTADOS] Excepción manejada en handlePriceUnit:", err);
                if (line && (line.price_unit === undefined || line.price_unit === null)) {
                    const product = line.product_id || line.product;
                    line.price_unit = product?.lst_price ?? product?.list_price ?? 0;
                }
                return;
            }
        }

        if (line && (line.price_unit === undefined || line.price_unit === null)) {
            const product = line.product_id || line.product;
            line.price_unit = product?.lst_price ?? product?.list_price ?? 0;
        }
    },
});
