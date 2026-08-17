
import { Component } from "@odoo/owl";

/**
 * Fila de lista reutilizable del POS de entintado.
 *
 * La fila solo aporta la estructura (izquierda · cuerpo · derecha) y el clic;
 * QUÉ va en cada zona lo deciden los slots, de modo que sirve igual para una
 * miniatura de producto, una muestra de color o un precio sin acoplarse a
 * ningún modelo concreto.
 *
 * Slots:
 *   - leading   (opcional) imagen, muestra de color o insignia a la izquierda
 *   - title     etiqueta principal
 *   - subtitle  (opcional) metadatos bajo el título
 *   - trailing  (opcional) valor a la derecha (precio, puntos…)
 */
export class TintListRow extends Component {
    static template = "entintados_pdv.TintListRow";
    static props = {
        onClick: { type: Function, optional: true },
        slots: { type: Object, optional: true },
    };
    static defaultProps = {
        onClick: () => {},
    };
}
