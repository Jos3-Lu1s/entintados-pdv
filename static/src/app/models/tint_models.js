/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Base } from "@point_of_sale/app/models/related_models";

export class TintCollection extends Base {
    static pythonModel = "tint.collection";
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

registry.category("pos_available_models").add(
    TintCollection.pythonModel,
    TintCollection
);

registry.category("pos_available_models").add(
    TintColor.pythonModel,
    TintColor
);

registry.category("pos_available_models").add(
    TintSize.pythonModel,
    TintSize
);

registry.category("pos_available_models").add(
    TintBaseType.pythonModel,
    TintBaseType
);

registry.category("pos_available_models").add(
    TintBaseCapacity.pythonModel,
    TintBaseCapacity
);

registry.category("pos_available_models").add(
    TintColorFormula.pythonModel,
    TintColorFormula
);

registry.category("pos_available_models").add(
    TintColorFormulaLine.pythonModel,
    TintColorFormulaLine
);