import { describe, expect, test } from "@odoo/hoot";
import { cashierEditRights } from "@entintados_pdv/app/utils/cashier_permissions";

const openConfig = { restrict_price_control: false, manual_discount: true };

function cashier(values = {}) {
    return { _role: "cashier", _can_edit_price: true, _can_edit_discount: true, ...values };
}

describe("entintados_pdv: cashier_permissions", () => {
    test("las llaves de Ventas deciden con los controles del core abiertos", () => {
        for (const price of [true, false]) {
            for (const discount of [true, false]) {
                const rights = cashierEditRights(
                    cashier({ _can_edit_price: price, _can_edit_discount: discount }),
                    openConfig
                );
                expect(rights).toEqual({ price, discount });
            }
        }
    });

    test("restrict_price_control niega el precio salvo al rol manager", () => {
        const config = { ...openConfig, restrict_price_control: true };
        expect(cashierEditRights(cashier(), config)).toEqual({ price: false, discount: true });
        expect(cashierEditRights(cashier({ _role: "manager" }), config)).toEqual({
            price: true,
            discount: true,
        });
    });

    test("el rol manager no da el permiso de Ventas", () => {
        const manager = cashier({ _role: "manager", _can_edit_price: false, _can_edit_discount: false });
        expect(cashierEditRights(manager, openConfig)).toEqual({ price: false, discount: false });
    });

    test("manual_discount apagado niega el descuento aunque haya grupo", () => {
        const config = { ...openConfig, manual_discount: false };
        expect(cashierEditRights(cashier(), config)).toEqual({ price: true, discount: false });
    });

    test("el rol minimal niega los dos", () => {
        expect(cashierEditRights(cashier({ _role: "minimal" }), openConfig)).toEqual({
            price: false,
            discount: false,
        });
    });

    test("sin cajero o sin las llaves niega los dos", () => {
        expect(cashierEditRights(null, openConfig)).toEqual({ price: false, discount: false });
        expect(cashierEditRights({ _role: "cashier" }, openConfig)).toEqual({
            price: false,
            discount: false,
        });
        expect(cashierEditRights(cashier({ _can_edit_price: 1 }), openConfig).price).toBe(false);
    });
});
