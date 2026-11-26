/**
 * Permiso del cajero activo para teclear precio y descuento en la caja.
 *
 * Suma dos reglas y ambas deben permitir:
 * - la de Ventas: `_can_edit_price` y `_can_edit_discount`, que el servidor agrega al cajero
 *   (`res.users` o `hr.employee`) al cargar la caja;
 * - la del core: `restrict_price_control`, `manual_discount` y el rol `minimal` de `pos_hr`.
 *
 * Si falta el cajero o la llave, se niega.
 */
export function cashierEditRights(cashier, config) {
    if (!cashier) {
        return { price: false, discount: false };
    }
    const minimal = cashier._role === "minimal";
    const corePrice = !config?.restrict_price_control || cashier._role === "manager";
    return {
        price: cashier._can_edit_price === true && corePrice && !minimal,
        discount: cashier._can_edit_discount === true && Boolean(config?.manual_discount) && !minimal,
    };
}
