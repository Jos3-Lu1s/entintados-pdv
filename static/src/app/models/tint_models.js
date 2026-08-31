
import { registry } from "@web/core/registry";
import { Base } from "@point_of_sale/app/models/related_models";

/**
 * Registro de modelos de entintado para su disponibilidad e instanciación en el POS.
 */
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

export class LinesProduct extends Base {
    static pythonModel = "lines.product";
}

export class LinesProductPresentation extends Base {
    static pythonModel = "lines.product.presentation";
}

const TINT_MODELS = [
    TintGallery,
    TintColor,
    TintSize,
    TintBaseType,
    TintBaseCapacity,
    TintColorFormula,
    TintColorFormulaLine,
    LinesProduct,
    LinesProductPresentation,
];

for (const model of TINT_MODELS) {
    registry.category("pos_available_models").add(model.pythonModel, model);
}
