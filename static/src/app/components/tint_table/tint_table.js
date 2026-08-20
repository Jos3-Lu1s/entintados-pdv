
import { Component } from "@odoo/owl";

/**
 * Componente de tabla reutilizable para listas de productos, colores y bases.
 * Renderiza encabezados dinámicos desde `columns` y el cuerpo mediante el slot `rows`.
 */
export class TintTable extends Component {
    static template = "entintados_pdv.TintTable";
    static props = {
        columns: { type: Array },
        slots: { type: Object, optional: true },
    };
}
