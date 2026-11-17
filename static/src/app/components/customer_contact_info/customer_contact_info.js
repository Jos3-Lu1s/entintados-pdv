import { Component } from "@odoo/owl";

/**
 * Componente para mostrar información detallada del contacto/cliente en el Punto de Venta:
 * datos fiscales, comerciales, límites de crédito, deuda actual y saldo de anticipo disponible.
 */
export class CustomerContactInfo extends Component {
    static template = "entintados_pdv.CustomerContactInfo";
    static props = {
        partner: { type: Object, optional: true },
    };
}
