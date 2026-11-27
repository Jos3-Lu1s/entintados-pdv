import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";
import {
    comboPayloadFromSaleLines,
    currentRulesResetValues,
    isImportedSaleLine,
    isSaleComboItem,
    matchComboItemSaleLines,
    quotedTotal,
    snapshotDoses,
    tintColorantChildValues,
} from "@entintados_pdv/app/utils/sale_order_import";
import { computeTintedPriceDetails, isTintedBase } from "@entintados_pdv/app/utils/tint_order";
import { printableQuotations } from "@entintados_pdv/app/utils/quotation_print";

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
            const existingOrder = this.getOpenOrders().find((order) =>
                printableQuotations(order).some((quotation) => quotation.id === saleOrder.id)
            );
            if (existingOrder) {
                const orderNumber = existingOrder.getName() || existingOrder.pos_reference || existingOrder.id;
                this.notification.add(
                    _t("Esta cotización ya está en la orden %s del punto de venta.", orderNumber),
                    { type: "warning" }
                );
                return;
            }
            // The quotation sets its own customer: lines already in the ticket would be
            // repriced for a customer they were not captured for.
            if (this.getOrder().lines.length) {
                this.notification.add(
                    _t("El ticket actual tiene productos. La cotización se abrirá en una orden nueva."),
                    { type: "warning" }
                );
                this.addNewOrder();
            }
            const order = this.getOrder();
            if (saleOrder.partner_id) {
                order.setPartner(saleOrder.partner_id);
            }
            const fiscalPosition = saleOrder.fiscal_position_id;
            order.update({ fiscal_position_id: fiscalPosition });
            await this.settleSO(saleOrder, fiscalPosition);
            this.selectOrderLine(order, order.lines.at(-1));
        } finally {
            this._processingSaleOrder = false;
        }
    },

    /** «Liquidar»: las líneas traídas se valoran con las reglas vigentes de la caja. */
    async settleSO(saleOrder, orderFiscalPos) {
        // Combo products are rebuilt under their package: the native flow would add them flat.
        const packageView = Object.create(saleOrder, {
            order_line: { value: saleOrder.order_line.filter((line) => !isSaleComboItem(line)) },
        });
        const result = await super.settleSO(packageView, orderFiscalPos);
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
        const saleLine = values.sale_order_line_id;
        const recipe = saleLine?.tint_snapshot;
        let colorants = [];
        if (recipe?.base_only) {
            // Colorant children only show the formula; its stock comes from the frozen recipe.
            colorants = tintColorantChildValues(snapshotDoses(this.models, recipe), values.qty);
            values = { ...values, is_tinted_base: true, tint_color_code: recipe.color_code || false,
                       customer_note: saleLine.name,
                       combo_line_ids: colorants.map((child) => ["create", {
                           ...child,
                           order_id: this.getOrder(),
                           manual_price: true,
                           customer_note: "",
                           tax_ids: (child.product_id.product_tmpl_id?.taxes_id || []).map((tax) => ["link", tax]),
                       }]) };
        }
        const itemSaleLines = this._saleComboItemLines(values);
        if (itemSaleLines) {
            const comboValues = { ...values, payload: comboPayloadFromSaleLines(saleLine, itemSaleLines) };
            await this.handleComboProduct(comboValues, this.getOrder(), true);
            values = { ...values, combo_line_ids: comboValues.combo_line_ids || [] };
        }
        const line = await super.addLineToCurrentOrder(values, options, configure);
        if (line && itemSaleLines) {
            this._linkComboItemsToSaleLines(line, itemSaleLines, values.sale_order_origin_id);
        }
        if (line && colorants.length) {
            // Same marks as a tint captured in the POS (`addTintedBaseToOrder`).
            line.is_tinted_base = true;
            line.combo_line_ids?.forEach((child, index) => {
                child.is_tint_colorant = true;
                child.unit_points ??= colorants[index]?.unit_points;
            });
        }
        return line;
    },

    /** Productos de Ventas del paquete de combo que se agrega, o `null` si no es un paquete traído. */
    _saleComboItemLines(values) {
        const saleLine = values.sale_order_line_id;
        if (!saleLine || values.product_id?.type !== "combo") {
            return null;
        }
        const saleOrder = this.models["sale.order"].get(values.sale_order_origin_id?.id);
        return (saleOrder?.order_line || []).filter(
            (line) => isSaleComboItem(line) && (line.linked_line_id?.id ?? line.linked_line_id) === saleLine.id
        );
    },

    /**
     * Liga cada producto del combo a su línea de venta y guarda lo cotizado, para escribir lo
     * cobrado al sincronizar y para el aviso de diferencia de total.
     */
    _linkComboItemsToSaleLines(packageLine, itemSaleLines, saleOrder) {
        for (const [itemLine, itemSaleLine] of matchComboItemSaleLines(packageLine.combo_line_ids, itemSaleLines)) {
            itemLine.update({ sale_order_line_id: itemSaleLine, sale_order_origin_id: saleOrder });
            itemSaleLine.quotedConverted = {
                product_uom_qty: itemSaleLine.product_uom_qty,
                price_total: itemSaleLine.price_total,
            };
        }
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

patch(OrderSummary.prototype, {
    /** Un combo traído de Ventas no se reconfigura: sus productos están ligados a la cotización. */
    async onOrderlineLongPress(event, line) {
        const packageLine = line?.combo_parent_id || line;
        if (packageLine?.product_id?.type === "combo" && packageLine.sale_order_line_id) {
            const quotation = packageLine.sale_order_origin_id || packageLine.sale_order_line_id.order_id;
            this.pos.notification.add(
                _t("El combo «%(combo)s» viene de la cotización %(quotation)s: cambia la cantidad o quítalo. Para cambiar sus productos, edítalo en Ventas.", {
                    combo: packageLine.product_id.display_name || "",
                    quotation: quotation?.name || "",
                }),
                { type: "warning" }
            );
            return false;
        }
        return await super.onOrderlineLongPress(event, line);
    },
});
