/** @odoo-module **/

import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { resolveAgreement } from "@entintados_pdv/app/utils/agreement_rules";
import { findPricelistRule, pricelistOriginLabel } from "@entintados_pdv/app/utils/pricelist_rules";
import { isTintColorant, isTintedBase } from "@entintados_pdv/app/utils/tint_order";
import {
    comboItemAgreementValues,
    comboItemPriceLabel,
    distributeComboPrice,
    isComboItem,
    isComboPackage,
    splitComboLines,
} from "@entintados_pdv/app/utils/combo_rules";

const MANUAL_PRICE_LABEL = "Modificado de forma manual";
const TINT_PRICE_LABEL = "Precio del entintado: base más colorantes";

function isAdvanceLine(order, line) {
    const nativeAdvanceProduct = order.config?.down_payment_product_id;
    return Boolean(
        line.product_id?.product_tmpl_id?.is_accounting_advance ||
            line.advance_source_ref ||
            (line.sale_order_origin_id && nativeAdvanceProduct && line.product_id?.id === nativeAdvanceProduct.id)
    );
}

/** Precio tecleado por el cajero: lo conservan la cantidad, la tarifa y el cliente. */
function hasTypedPrice(line) {
    return line.price_type === "manual" && line.price_origin === "manual";
}

function hasManualPricing(line) {
    return hasTypedPrice(line) || line.pricing_rule_type === "manual";
}

function clearAgreement(line) {
    line.discount = 0;
    line.pricing_rule_type = "none";
    line.pricing_rule_origin = "";
    line.pricing_rule_id = false;
}

function writeFixedPriceAgreement(line, agreement) {
    line.discount = 0;
    line.pricing_rule_type = "fixed_price";
    line.pricing_rule_origin = agreement.originLabel;
    line.pricing_rule_id = agreement.rule || false;
    line.promo_reward_id = false;
    line.fixed_price_locked = true;
}

/** Descuento del acuerdo con su badge, o ninguno. */
function writeDiscountAgreement(line, agreement) {
    if (agreement.type !== "discount") {
        clearAgreement(line);
        return;
    }
    line.discount = Number(agreement.discount || 0);
    line.pricing_rule_type = agreement.originType;
    line.pricing_rule_origin = agreement.originLabel;
    line.pricing_rule_id = agreement.rule || false;
}

/**
 * Precio de tarifa y acuerdo comercial de las líneas, con las reglas de Ventas: el precio
 * fijo del cliente gana; el precio y el descuento tecleados sobreviven a la cantidad, a la
 * tarifa y al cliente; la promoción de la línea la decide el motor de promociones.
 *
 * Escribe los campos directamente: `setUnitPrice` y `setDiscount` llevan los bloqueos que
 * son para el cajero.
 */
patch(PosOrder.prototype, {
    setPartner(partner) {
        const result = super.setPartner(...arguments);
        this.refreshOrderPricing({ partnerChanged: true });
        return result;
    },

    setPricelist(pricelist) {
        const result = super.setPricelist(...arguments);
        this.refreshOrderPricing();
        return result;
    },

    /**
     * @param {{ partnerChanged?: boolean }} options - Con `partnerChanged`, el precio fijo
     *     del cliente nuevo gana también sobre el precio y el descuento manuales.
     */
    refreshOrderPricing({ partnerChanged = false } = {}) {
        if (this.finalized) {
            return;
        }
        const partner = this.getPartner();
        for (const line of this.lines || []) {
            this._refreshLinePricing(line, partner, partnerChanged);
        }
    },

    _refreshLinePricing(line, partner, partnerChanged) {
        // Un reembolso conserva lo que se cobró; una recompensa nativa la maneja el core.
        if (line.refunded_orderline_id || line.is_reward_line || line.reward_id) {
            return;
        }
        // El producto de un combo no tiene acuerdo propio: lo resuelve su paquete.
        if (isComboItem(line)) {
            return;
        }
        if (isComboPackage(line)) {
            this._refreshComboPricing(line, partner, partnerChanged);
            return;
        }
        if (isAdvanceLine(this, line)) {
            clearAgreement(line);
            line.price_origin = "manual";
            line.price_origin_label = MANUAL_PRICE_LABEL;
            return;
        }
        if (isTintColorant(line)) {
            clearAgreement(line);
            line.price_origin = "tint";
            line.price_origin_label = TINT_PRICE_LABEL;
            return;
        }

        const agreement = resolveAgreement(this.models, partner, line.product_id, {
            currency: this.currency,
        });

        if (isTintedBase(line)) {
            // El precio lo calcula el flujo de entintado.
            line.price_origin = "tint";
            line.price_origin_label = TINT_PRICE_LABEL;
            this._refreshTintedBaseAgreement(line, agreement);
            return;
        }

        if (agreement.type === "fixed_price" && (partnerChanged || !hasManualPricing(line))) {
            this._writeLinePrice(line, agreement.price);
            line.price_type = "manual";
            line.price_origin = "fixed_price";
            line.price_origin_label = agreement.originLabel;
            writeFixedPriceAgreement(line, agreement);
            return;
        }

        if (line.fixed_price_locked) {
            line.fixed_price_locked = false;
            line.price_type = "original";
        }
        if (line.price_type === "original") {
            this._refreshListPrice(line);
        }

        if (line.promo_reward_id || line.pricing_rule_type === "manual") {
            return;
        }
        writeDiscountAgreement(line, agreement);
    },

    /**
     * Acuerdo del combo, resuelto sobre el paquete como en Ventas: el paquete vale 0, su precio
     * (tarifa o precio fijo) se reparte entre sus productos y estos copian su descuento y badge.
     */
    _refreshComboPricing(packageLine, partner, partnerChanged) {
        const product = packageLine.product_id;
        const agreement = resolveAgreement(this.models, partner, product, { currency: this.currency });
        let total;
        packageLine.price_unit = 0;
        if (agreement.type === "fixed_price" && (partnerChanged || !hasManualPricing(packageLine))) {
            writeFixedPriceAgreement(packageLine, agreement);
            packageLine.price_origin = "fixed_price";
            packageLine.price_origin_label = agreement.originLabel;
            total = agreement.price;
        } else {
            const pricelist = this.pricelist_id;
            packageLine.fixed_price_locked = false;
            total = product.product_tmpl_id.getPrice(pricelist, 1, 0, false, product);
            const rule = findPricelistRule(pricelist, product, 1);
            packageLine.price_origin = rule ? "pricelist" : "list";
            packageLine.price_origin_label = pricelistOriginLabel(pricelist, rule);
            if (!packageLine.promo_reward_id && packageLine.pricing_rule_type !== "manual") {
                writeDiscountAgreement(packageLine, agreement);
            }
        }

        const { freeLines, extraLines, packageQty } = splitComboLines(packageLine);
        const prices = distributeComboPrice({
            total,
            packageQty,
            freeLines,
            extraLines,
            rounding: this.currency,
        });
        for (const [itemLine, price] of prices) {
            this._writeLinePrice(itemLine, price);
            itemLine.price_origin = "combo";
            itemLine.price_origin_label = comboItemPriceLabel(packageLine, itemLine);
        }
        this.syncComboItems(packageLine);
    },

    /** Copia a los productos del combo el descuento y el badge de su paquete. */
    syncComboItems(packageLine) {
        const values = comboItemAgreementValues(packageLine);
        for (const itemLine of packageLine.combo_line_ids || []) {
            if (!isComboItem(itemLine)) {
                continue;
            }
            Object.assign(itemLine, values);
            itemLine.promo_reward_id = false;
        }
    },

    _refreshTintedBaseAgreement(line, agreement) {
        if (line.promo_reward_id) {
            return;
        }
        if (agreement.type === "fixed_price") {
            writeFixedPriceAgreement(line, agreement);
            return;
        }
        line.fixed_price_locked = false;
        writeDiscountAgreement(line, agreement);
    },

    /** Precio de la tarifa del pedido y su origen. */
    _refreshListPrice(line) {
        const product = line.product_id;
        const pricelist = this.pricelist_id;
        const quantity = line.getQuantity();
        const price = product.product_tmpl_id.getPrice(
            pricelist,
            quantity,
            line.getPriceExtra(),
            false,
            product,
            line,
            []
        );
        this._writeLinePrice(line, price);
        const rule = findPricelistRule(pricelist, product, quantity);
        line.price_origin = rule ? "pricelist" : "list";
        line.price_origin_label = pricelistOriginLabel(pricelist, rule);
    },

    _writeLinePrice(line, price) {
        const precision = this.models["decimal.precision"].find((dp) => dp.name === "Product Price");
        const value = Number(price) || 0;
        line.price_unit = precision ? precision.round(value) : value;
    },
});

/** Aviso de por qué no se teclea el precio de una línea de combo, o `null`. */
function comboPriceLockMessage(line) {
    if (isComboItem(line)) {
        return _t(
            "No se puede cambiar el precio de «%(product)s»: es parte del combo «%(package)s» y su precio sale del reparto del paquete. Cambia el precio o el descuento en el paquete.",
            {
                product: line.product_id?.display_name || "",
                package: line.combo_parent_id?.product_id?.display_name || "",
            }
        );
    }
    if (isComboPackage(line)) {
        return _t("El precio de un combo es el reparto de sus productos: no se teclea.");
    }
    return null;
}

patch(OrderSummary.prototype, {
    /** Precio tecleado: queda manual solo si cambió, como `_onchange_price_unit_manual`. */
    async setLinePrice(line, price) {
        const lockMessage = comboPriceLockMessage(line);
        if (lockMessage) {
            this.numberBuffer.reset();
            this.pos.notification.add(lockMessage, { type: "warning" });
            return;
        }
        const previousPrice = line.price_unit;
        const previousType = line.price_type;
        const result = await super.setLinePrice(...arguments);
        if (line.price_unit === previousPrice) {
            line.price_type = previousType;
            return result;
        }
        line.price_origin = "manual";
        line.price_origin_label = MANUAL_PRICE_LABEL;
        return result;
    },
});
