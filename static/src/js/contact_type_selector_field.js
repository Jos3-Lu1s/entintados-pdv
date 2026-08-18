import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Selector de campos boolean mostrados como pastillas/insignias interactivas.
 */
export class BooleanGroupSelectorField extends Component {
    static template = "entintados_pdv.BooleanGroupSelectorField";
    static props = {
        ...standardFieldProps,
        fieldsConfig: { type: Array },
        contextKey: { type: [String, Boolean], optional: true },
        groupValues: { type: Object, optional: true },
    };
    static defaultProps = {
        contextKey: false,
        groupValues: {},
    };
    // Paleta por defecto para colores de tags nativos de Odoo (o_tag_color_N).
    static COLOR_PALETTE = [1, 4, 3, 6, 2, 5, 7, 8, 9, 10, 11, 0];

    setup() {
        this.state = useState({ values: {} });
        useRecordObserver((record) => {
            const values = {};
            for (const option of this.props.fieldsConfig) {
                values[option.field] = record.data[option.field];
            }
            this.state.values = values;
        });
    }

    /** Opciones de campos a mostrar, filtradas por grupo de contexto si aplica. */
    get options() {
        const { contextKey, groupValues, fieldsConfig } = this.props;
        if (!contextKey) {
            return fieldsConfig;
        }
        const currentValue = this.props.record.context[contextKey];
        const activeGroup = Object.keys(groupValues).find(
            (group) => groupValues[group] === currentValue
        );
        if (!activeGroup) {
            return fieldsConfig;
        }
        return fieldsConfig.filter((option) => option.group === activeGroup);
    }

    /** Obtiene la etiqueta del campo (opción configurada o string del modelo). */
    getLabel(option) {
        if (option.label) {
            return option.label;
        }
        const field = this.props.record.fields[option.field];
        return field ? field.string : option.field;
    }

    /** Obtiene el índice de color para la opción según configuración o paleta. */
    getColor(option) {
        if (option.color !== undefined && option.color !== false) {
            return option.color;
        }
        const index = this.props.fieldsConfig.findIndex((o) => o.field === option.field);
        const palette = this.constructor.COLOR_PALETTE;
        return palette[index % palette.length];
    }

    isActive(fieldName) {
        return Boolean(this.state.values[fieldName]);
    }

    async onSelect(fieldName) {
        if (this.props.readonly) {
            return;
        }
        const newValue = !this.state.values[fieldName];
        this.state.values[fieldName] = newValue;
        await this.props.record.update({ [fieldName]: newValue }, { save: true });
    }

    /** Maneja la activación por teclado (Enter/Espacio) sobre el elemento. */
    onKeydown(ev, fieldName) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.onSelect(fieldName);
        }
    }
}

export const booleanGroupSelectorField = {
    component: BooleanGroupSelectorField,
    displayName: _t("Selector agrupado (pastillas)"),
    supportedTypes: ["boolean"],
    isEmpty: () => false,
    supportedOptions: [
        {
            label: _t("Campos"),
            name: "fields",
            type: "string",
            help: _t(
                "Lista de campos boolean a mostrar como pastillas, p. ej.: " +
                    "[{'field': 'x', 'group': 'g'}, ...]. 'label' es opcional " +
                    "(por defecto usa el string del campo)."
            ),
        },
        {
            label: _t("Clave de contexto"),
            name: "context_key",
            type: "string",
            help: _t(
                "Clave de contexto que determina qué grupo mostrar (opcional). " +
                    "Sin esta opción siempre se muestran todos los campos."
            ),
        },
        {
            label: _t("Valores de grupo"),
            name: "group_values",
            type: "string",
            help: _t(
                "Diccionario {grupo: valor_de_contexto} que asocia cada grupo " +
                    "con el valor de la clave de contexto que lo activa."
            ),
        },
    ],
    extractProps({ options }, dynamicInfo) {
        return {
            fieldsConfig: options.fields || [],
            contextKey: options.context_key || false,
            groupValues: options.group_values || {},
            readonly: dynamicInfo.readonly,
        };
    },
};

registry.category("fields").add("boolean_group_selector", booleanGroupSelectorField);
