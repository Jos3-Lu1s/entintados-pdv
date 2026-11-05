import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { usePopover } from "@web/core/popover/popover_hook";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatMonetary } from "@web/views/fields/formatters";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { promoHintLines } from "./promo_hint_text";

export class PromoHintPopover extends Component {
    static template = "entintados_pdv.PromoHintPopover";
    static props = {
        rows: Array,
        selectable: Boolean,
        onSelect: Function,
        close: { type: Function, optional: true },
    };

    select(row) {
        if (!this.props.selectable || row.applied) {
            return;
        }
        this.props.close?.();
        this.props.onSelect(row.rewardId);
    }
}

/**
 * Estrella de la línea de venta con promociones: abre la lista de las que le tocan, con cuánto
 * cambia el importe, y un clic aplica la elegida. Fuera de la lista (p. ej. en el formulario de
 * la línea) solo informa.
 */
export class PromoHintField extends Component {
    static template = "entintados_pdv.PromoHintField";
    static props = { ...standardFieldProps, selectable: { type: Boolean, optional: true } };
    static defaultProps = { selectable: true };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.popover = usePopover(PromoHintPopover, { position: "left" });
    }

    get info() {
        return this.props.record.data[this.props.name];
    }

    get hasPromo() {
        return Boolean(this.info?.options?.some((option) => option.applied));
    }

    get title() {
        return this.hasPromo ? _t("Promoción aplicada") : _t("Promoción disponible");
    }

    onClick(ev) {
        if (this.popover.isOpen) {
            this.popover.close();
            return;
        }
        const currencyId = this.props.record.data.currency_id?.id;
        this.popover.open(ev.currentTarget, {
            rows: promoHintLines(this.info, _t, (amount) => formatMonetary(amount, { currencyId })),
            selectable: this.props.selectable,
            onSelect: (rewardId) => this.setPromotion(rewardId),
        });
    }

    async setPromotion(rewardId) {
        const root = this.props.record.model.root;
        if (!(await root.save())) {
            return;
        }
        const lineId = this.props.record.resId;
        if (!lineId) {
            this.notification.add(_t("Guarda la línea antes de elegir su promoción."), { type: "warning" });
            return;
        }
        await this.orm.call("sale.order.line", "action_set_line_promotion", [[lineId], rewardId]);
        await root.load();
    }
}

export const promoHintField = {
    component: PromoHintField,
    displayName: _t("Promoción de la línea (icono)"),
    supportedTypes: ["json"],
    supportedOptions: [{ label: _t("Permite elegir"), name: "selectable", type: "boolean" }],
    extractProps: ({ options }) => ({ selectable: options.selectable !== false }),
    fieldDependencies: [{ name: "currency_id", type: "many2one", relation: "res.currency" }],
};

registry.category("fields").add("promo_hint", promoHintField);
