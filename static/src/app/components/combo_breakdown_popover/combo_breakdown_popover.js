import { Component } from "@odoo/owl";

/**
 * Desglose del reparto del precio de un combo en la caja. Usa la plantilla de Ventas: los datos
 * salen de `comboBreakdown`, que devuelve la misma forma que `buildComboBreakdown`.
 */
export class PosComboBreakdownPopover extends Component {
    static template = "entintados_pdv.ComboBreakdownPopover";
    static props = {
        breakdown: Object,
        close: { type: Function, optional: true },
    };
}
