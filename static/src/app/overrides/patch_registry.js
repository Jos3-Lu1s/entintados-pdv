/** @odoo-module */

import { Registry } from "@web/core/registry";

/**
 * En Odoo 19, CustomSelectCreateDialog fue integrado en point_of_sale core
 * (@point_of_sale/app/components/custom_select_create_dialog/custom_select_create_dialog)
 * y simultáneamente está registrado en pos_settle_due
 * (@pos_settle_due/app/views/view_dialogs/select_create_dialog).
 * Al cargarse ambos módulos en point_of_sale._assets_pos, el segundo intento de
 * registrar la clave "custom_select_create" en la categoría "dialogs" produce
 * un DuplicatedKeyError que bloquea la carga del Punto de Venta.
 *
 * Este parche intercepta Registry.prototype.add para que cuando se registre
 * "custom_select_create" en "dialogs" y ya exista en el registro, se aplique
 * autom\u00e1ticamente force: true, evitando que falle la carga del POS.
 */
const originalAdd = Registry.prototype.add;

Registry.prototype.add = function (key, value, options = {}) {
    if (this.name === "dialogs" && key === "custom_select_create" && key in this.content) {
        options = { ...options, force: true };
    }
    return originalAdd.call(this, key, value, options);
};
