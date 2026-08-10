
import { registry } from "@web/core/registry";
import { Base } from "@point_of_sale/app/models/related_models";

/**
 * Modelos de entintado disponibles en caja.
 *
 * Ninguno necesita comportamiento propio en el cliente todavía: existen para
 * que el POS sepa instanciarlos. Se declaran en una lista y se registran en
 * bucle en lugar de repetir siete bloques idénticos; añadir un modelo es
 * añadir una línea.
 */
export class TintCollection extends Base {
    static pythonModel = "tint.collection";
}

export class TintGallery extends Base {
    static pythonModel = "tint.gallery";
}

export class TintColor extends Base {
    static pythonModel = "tint.color";
}

export class TintSize extends Base {
    static pythonModel = "tint.size";
}

export class TintBaseType extends Base {
    static pythonModel = "tint.base.type";
}

export class TintBaseCapacity extends Base {
    static pythonModel = "tint.base.capacity";
}

export class TintColorFormula extends Base {
    static pythonModel = "tint.color.formula";
}

export class TintColorFormulaLine extends Base {
    static pythonModel = "tint.color.formula.line";
}

const TINT_MODELS = [
    TintCollection,
    TintGallery,
    TintColor,
    TintSize,
    TintBaseType,
    TintBaseCapacity,
    TintColorFormula,
    TintColorFormulaLine,
];

for (const model of TINT_MODELS) {
    registry.category("pos_available_models").add(model.pythonModel, model);
}
