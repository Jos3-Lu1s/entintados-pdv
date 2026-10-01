import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { usePopover } from "@web/core/popover/popover_hook";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/** Icono y color por origen del precio de la línea de venta. */
const PRICE_ORIGINS = {
    list: { icon: "fa-tag", color: "text-muted" },
    pricelist: { icon: "fa-list-alt", color: "text-info" },
    fixed_price: { icon: "fa-lock", color: "text-success" },
    manual: { icon: "fa-pencil", color: "text-warning" },
};

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
 */
export class PriceOriginField extends Component {
    static template = "entintados_pdv.PriceOriginField";
    static props = { ...standardFieldProps };

    setup() {
        this.popover = usePopover(PriceOriginPopover, { position: "left" });
    }

    get origin() {
        return PRICE_ORIGINS[this.props.record.data[this.props.name]] || null;
    }

    get title() {
        const value = this.props.record.data[this.props.name];
        const selection = this.props.record.fields[this.props.name].selection || [];
        const option = selection.find(([key]) => key === value);
        return option ? option[1] : "";
    }

    onClick(ev) {
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
    fieldDependencies: [{ name: "price_origin_label", type: "char" }],
};

registry.category("fields").add("price_origin_icon", priceOriginField);
