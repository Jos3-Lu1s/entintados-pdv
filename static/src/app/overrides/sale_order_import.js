import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";
import {
    currentRulesResetValues,
    isImportedSaleLine,
    quotedTotal,
    snapshotDoses,
} from "@entintados_pdv/app/utils/sale_order_import";
import { computeTintedPriceDetails, isTintedBase } from "@entintados_pdv/app/utils/tint_order";

/**
 * Cotizaciones y pedidos de Ventas traídos a la caja con «Cotización/Orden» (`pos_sale`).
 * Las reglas puras viven en `utils/sale_order_import.js`.
 */
patch(PosStore.prototype, {
    async _processSaleOrder(saleOrder) {
        if (this._processingSaleOrder) {
            return;
        }
        this._processingSaleOrder = true;
        try {
            // The quotation sets its own customer: lines already in the ticket would be
            // repriced for a customer they were not captured for.
            if (this.getOrder().lines.length) {
                this.notification.add(
                    _t("El ticket actual tiene productos: la cotización se abrió en un ticket nuevo."),
                    { type: "warning" }
                );
                this.addNewOrder();
            }
            return await super._processSaleOrder(...arguments);
        } finally {
            this._processingSaleOrder = false;
        }
    },

    /** «Liquidar»: las líneas traídas se valoran con las reglas vigentes de la caja. */
    async settleSO(saleOrder, orderFiscalPos) {
        const result = await super.settleSO(...arguments);
        const order = this.getOrder();
        // The native flow copies the quotation price and discount; recalculation runs
        // on every added line, so the final valuation is a single pass at the end.
        const keptQuotedPrice = [];
        for (const line of order.lines.filter(isImportedSaleLine)) {
            if (!isTintedBase(line)) {
                Object.assign(line, currentRulesResetValues());
            } else if (!this._repriceTintedBase(order, line)) {
                keptQuotedPrice.push(line);
            }
        }
        this._applyCustomerPricelist(order);
        order.refreshOrderPricing();
        for (const line of keptQuotedPrice) {
            line.price_origin = "manual";
            line.price_origin_label = _t("Precio de la cotización: falta un colorante en la caja");
        }
        // The quotation promotions are not copied: the POS engine decides them.
        this._refreshLinePromotions(order);
        this._notifyQuotedTotalChange(order, saleOrder);
        return result;
    },

    /** Avisa si las líneas traídas valen distinto que en la cotización. */
    _notifyQuotedTotalChange(order, saleOrder) {
        const lines = order.lines.filter(
            (line) => isImportedSaleLine(line) && line.sale_order_origin_id &&
                line.sale_order_origin_id.id === saleOrder.id
        );
        if (!lines.length) {
            return;
        }
        const quoted = quotedTotal(
            lines.map((line) => ({ qty: line.qty, quotedSaleLine: line.sale_order_line_id.quotedConverted }))
        );
        const current = lines.reduce((total, line) => total + line.priceIncl, 0);
        if (this.currency.isZero(current - quoted)) {
            return;
        }
        const formatCurrency = this.env.utils.formatCurrency;
        this.notification.add(
            _t("La cotización %(quotation)s valía %(quoted)s; con las reglas vigentes el ticket queda en %(current)s.", {
                quotation: saleOrder.name || "",
                quoted: formatCurrency(quoted),
                current: formatCurrency(current),
            }),
            { type: "warning" }
        );
    },

    /**
     * Precio del entintado con la base y los colorantes de hoy sobre los puntos congelados de
     * la fórmula; el acuerdo o la promoción vigentes se aplican después, en el recálculo.
     * Devuelve `false` si algún colorante no está cargado: la línea conserva el precio cotizado.
     */
    _repriceTintedBase(order, line) {
        const doses = snapshotDoses(this.models, line.sale_order_line_id?.tint_snapshot);
        const missing = doses.filter((dose) => !dose.colorant);
        if (missing.length) {
            this.notification.add(
                _t("No están cargados en la caja los colorantes %(colorants)s: «%(product)s» conserva el precio de la cotización.", {
                    colorants: missing.map((dose) => dose.name || dose.colorantId).join(", "),
                    product: line.getFullProductName?.() || line.product_id?.display_name || "",
                }),
                { type: "warning" }
            );
            Object.assign(line, { price_type: "manual" });
            return false;
        }
        const { finalPrice } = computeTintedPriceDetails(this, line.product_id, null, order.getPartner(), doses);
        Object.assign(line, currentRulesResetValues(), { price_type: "manual" });
        order._writeLinePrice(line, finalPrice);
        line.price_origin = "tint";
        return true;
    },

    /**
     * Tarifa del cliente si la caja la tiene cargada, si no la de la caja, como al asignar el
     * cliente en `updatePricelistAndFiscalPosition`. La tarifa de la cotización no se usa.
     */
    _applyCustomerPricelist(order) {
        if (this.config.use_presets && order.preset_id?.pricelist_id) {
            return;
        }
        const partnerPricelistId = order.getPartner()?.property_product_pricelist?.id;
        const pricelist =
            this.models["product.pricelist"].find((item) => item.id === partnerPricelistId) ||
            this.config.pricelist_id;
        order.setPricelist(pricelist);
    },

    async addLineToCurrentOrder(values, options = {}, configure = true) {
        const recipe = values.sale_order_line_id?.tint_snapshot;
        if (recipe?.base_only) {
            values = { ...values, is_tinted_base: true, tint_color_code: recipe.color_code || false,
                       customer_note: values.sale_order_line_id.name };
        }
        return super.addLineToCurrentOrder(values, options, configure);
    },
});

patch(PosOrderline.prototype, {
    /**
     * El core pasa la línea de venta ya convertida a la unidad del producto (`read_converted`):
     * se guarda su cantidad y su total para comparar después con la valoración de la caja.
     */
    setQuantityFromSOL(saleOrderLine) {
        if (this.sale_order_line_id) {
            this.sale_order_line_id.quotedConverted = {
                product_uom_qty: saleOrderLine.product_uom_qty,
                price_total: saleOrderLine.price_total,
            };
        }
        return super.setQuantityFromSOL(...arguments);
    },
});

patch(ControlButtons.prototype, {
    onClickQuotation() {
        const partner = this.pos.getOrder()?.getPartner();
        const domain = [
            ["state", "!=", "cancel"],
            ["invoice_status", "!=", "to invoice"],
            ["pos_has_active_invoice", "=", false],
            ["currency_id", "=", this.pos.currency.id],
            ["amount_unpaid", ">", 0],
        ];
        if (partner) {
            domain.push(["partner_id", "any", [["id", "child_of", [partner.id]]]]);
        }
        this.dialog.add(SelectCreateDialog, {
            resModel: "sale.order",
            noCreate: true,
            multiSelect: false,
            domain,
            context: this.partner ? { search_default_partner_id: this.partner.id } : {},
            onSelected: async ([orderId]) => {
                if (orderId) {
                    await this.pos.onClickSaleOrder(orderId);
                }
            },
        });
    },
});
