import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { usePopover } from "@web/core/popover/popover_hook";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import {
    buildComboBreakdown,
    ComboBreakdownPopover,
} from "../combo_breakdown/combo_breakdown_popover";

/** Icono y color por origen del precio de la línea de venta. */
const PRICE_ORIGINS = {
    list: { icon: "fa-tag", color: "text-muted" },
    pricelist: { icon: "fa-list-alt", color: "text-info" },
    fixed_price: { icon: "fa-lock", color: "text-success" },
    manual: { icon: "fa-pencil", color: "text-warning" },
    combo: { icon: "fa-cubes", color: "text-primary" },
};

const COMBO_PACKAGE = { icon: "fa-cubes", color: "text-primary" };

export class PriceOriginPopover extends Component {
    static template = "entintados_pdv.PriceOriginPopover";
    static props = {
        title: String,
        detail: { type: [String, Boolean], optional: true },
        close: { type: Function, optional: true },
    };
}

/**
 * Indicador compacto del origen del precio: icono de color que abre un popover con el detalle
 * (`price_origin_label`) al hacer clic. Sin icono en líneas históricas sin origen.
 * En la línea de un paquete combo abre el desglose del reparto, sea cual sea su `price_origin`.
 */
export class PriceOriginField extends Component {
    static template = "entintados_pdv.PriceOriginField";
    static props = { ...standardFieldProps };

    setup() {
        this.popover = usePopover(PriceOriginPopover, { position: "left" });
        this.comboPopover = usePopover(ComboBreakdownPopover, { position: "left" });
    }

    get isComboPackage() {
        return this.props.record.data.product_type === "combo";
    }

    get origin() {
        if (this.isComboPackage) {
            return COMBO_PACKAGE;
        }
        return PRICE_ORIGINS[this.props.record.data[this.props.name]] || null;
    }

    get title() {
        if (this.isComboPackage) {
            return _t("Desglose del combo");
        }
        const value = this.props.record.data[this.props.name];
        const selection = this.props.record.fields[this.props.name].selection || [];
        const option = selection.find(([key]) => key === value);
        return option ? option[1] : "";
    }

    onClick(ev) {
        if (this.isComboPackage) {
            if (this.comboPopover.isOpen) {
                this.comboPopover.close();
                return;
            }
            this.comboPopover.open(ev.currentTarget, {
                breakdown: buildComboBreakdown(this.props.record),
            });
            return;
        }
        if (this.popover.isOpen) {
            this.popover.close();
            return;
        }
        this.popover.open(ev.currentTarget, {
            title: this.title,
            detail: this.props.record.data.price_origin_label || false,
        });
    }
}

export const priceOriginField = {
    component: PriceOriginField,
    displayName: _t("Origen del precio (icono)"),
    supportedTypes: ["selection"],
    fieldDependencies: [
        { name: "price_origin_label", type: "char" },
        // Desglose del combo: la fila del paquete lee las de sus productos.
        { name: "product_type", type: "selection" },
        { name: "combo_item_id", type: "many2one", relation: "product.combo.item" },
        { name: "linked_line_id", type: "many2one", relation: "sale.order.line" },
        { name: "linked_virtual_id", type: "char" },
        { name: "virtual_id", type: "char" },
        { name: "combo_price_snapshot", type: "json" },
        { name: "price_unit", type: "float" },
        { name: "product_uom_qty", type: "float" },
        { name: "discount", type: "float" },
        { name: "price_subtotal", type: "monetary" },
        { name: "pricing_rule_origin", type: "char" },
    ],
};

registry.category("fields").add("price_origin_icon", priceOriginField);
