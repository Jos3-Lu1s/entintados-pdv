/** @odoo-module **/

import { resolveAgreement } from "@entintados_pdv/app/utils/agreement_rules";

/**
 * Acuerdo comercial vigente de la línea, en la moneda del pedido.
 *
 * Es el único punto por el que las promociones leen el acuerdo al que vuelve una línea.
 *
 * @param {Object} order - Pedido del PDV
 * @param {Object} line - Línea del pedido
 * @returns {Object} El mismo resultado que `resolveAgreement`
 */
export function agreementForLine(order, line) {
    return resolveAgreement(order?.models, order?.getPartner?.(), line?.product_id, {
        currency: order?.currency,
    });
}
