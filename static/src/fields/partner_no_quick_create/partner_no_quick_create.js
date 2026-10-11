import { patch } from "@web/core/utils/patch";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";

/** Un contacto creado solo con su nombre quedaría sin teléfono. */
const PARTNER_MODEL = "res.partner";

patch(Many2XAutocomplete.prototype, {
    addCreateSuggestion(params) {
        if (this.props.resModel === PARTNER_MODEL) {
            return false;
        }
        return super.addCreateSuggestion(params);
    },

    addNoRecordsSuggestion(params) {
        if (this.props.resModel === PARTNER_MODEL) {
            return !this.activeActions.createEdit;
        }
        return super.addNoRecordsSuggestion(params);
    },
});
