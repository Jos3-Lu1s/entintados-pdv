/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Selector de campos boolean mostrados como insignias.
 * Es un widget GENÉRICO, Toda la configuración vive en la vista, vía el
 * atributo `options` del campo:
 *
 *   <field name="is_customer" widget="boolean_group_selector" options="{
 *       'fields': [
 *           {'field': 'is_customer', 'group': 'sales', 'color': 1, 'icon': 'fa-shopping-cart'},
 *           {'field': 'is_supplier', 'group': 'purchase', 'color': 4, 'icon': 'fa-truck'},
 *           {'field': 'is_creditor', 'group': 'purchase', 'color': 3, 'icon': 'fa-money'},
 *           {'field': 'is_distributor', 'group': 'sales', 'color': 6, 'icon': 'fa-share-alt'},
 *       ],
 *       'context_key': 'res_partner_search_mode',
 *       'group_values': {'sales': 'customer', 'purchase': 'supplier'},
 *   }"/>
 *   <field name="is_supplier" column_invisible="1"/>
 *   <field name="is_creditor" column_invisible="1"/>
 *   <field name="is_distributor" column_invisible="1"/>
 *
 * - `fields`: lista de campos boolean a mostrar. Cada entrada admite:
 *     - `field` (obligatorio): nombre técnico del campo boolean.
 *     - `group` (opcional): para filtrar por `context_key`/`group_values`.
 *     - `label` (opcional): si se omite, se usa el `string` ya definido en
 *       el propio campo del modelo Python (una sola fuente de verdad).
 *     - `color` (opcional): número de color de la paleta nativa de tags de
 *       Odoo (clase `o_tag_color_N`, 0-11). Si se omite, se asigna
 *       automáticamente uno distinto por campo según su posición en
 *       `fields`, así el widget se ve bien sin configurar nada.
 *     - `icon` (opcional): clase de ícono Font Awesome (ej. 'fa-truck').
 * - `context_key` / `group_values`: opcionales. Permiten mostrar solo un
 *   subconjunto ("grupo") de las opciones según el valor de una clave de
 *   contexto. Si no se define `context_key`, siempre se muestran todos los
 *   campos de `fields`.
 *
 * Reutilizable en cualquier modelo y con cualquier conjunto de campos
 * boolean: para usarlo en otro caso no se toca este archivo, solo se
 * declara un `options` distinto en la vista correspondiente (Open/Closed:
 * cerrado a modificación, abierto a configuración).
 *
 * Requisito: cada campo listado en `fields` (salvo el que lleva el widget)
 * debe declararse también como <field> en el arch (puede ir con
 * column_invisible="1"/invisible="1") para que su valor viaje junto con el
 * registro; el widget no carga campos por su cuenta.
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
    // Paleta por defecto: índices de la paleta nativa de colores de tags
    // de Odoo (clases o_tag_color_0 .. o_tag_color_11).
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

    /**
     * Devuelve las opciones a mostrar. Si hay `contextKey` configurada y su
     * valor actual coincide con alguno de los `groupValues`, se filtra al
     * grupo correspondiente; en cualquier otro caso se muestran todas.
     */
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

    /**
     * Etiqueta a mostrar: la explícita en `options.fields[].label` si se
     * definió, o si no, el `string` del campo tal como está definido en el
     * modelo (fuente única, evita duplicar textos entre Python y la vista).
     */
    getLabel(option) {
        if (option.label) {
            return option.label;
        }
        const field = this.props.record.fields[option.field];
        return field ? field.string : option.field;
    }

    /**
     * Color estable por campo (índice de la paleta nativa de tags de Odoo,
     * clase `o_tag_color_N`): el explícito en `option.color`, o si no, uno
     * tomado de una paleta por defecto según la posición del campo en
     * `fieldsConfig` (no en `options`, que puede estar filtrada por
     * contexto — el color de un campo debe ser siempre el mismo).
     */
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

    /** El indicador es un <span>, no un <button>: activarlo por teclado
     * (Enter/Espacio) igual que un control nativo. */
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
