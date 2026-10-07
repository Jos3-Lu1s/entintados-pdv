import { patch } from "@web/core/utils/patch";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";

/** Un producto creado solo con su nombre quedaría sin referencia interna. */
const PRODUCT_MODELS = new Set(["product.product", "product.template"]);

patch(Many2XAutocomplete.prototype, {
    addCreateSuggestion(params) {
        if (PRODUCT_MODELS.has(this.props.resModel)) {
            return false;
        }
        return super.addCreateSuggestion(params);
    },

    addNoRecordsSuggestion(params) {
        if (PRODUCT_MODELS.has(this.props.resModel)) {
            return !this.activeActions.createEdit;
        }
        return super.addNoRecordsSuggestion(params);
    },
});
