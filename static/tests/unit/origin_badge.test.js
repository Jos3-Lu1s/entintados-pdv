import { describe, expect, test } from "@odoo/hoot";
import { formatOriginBadge } from "@entintados_pdv/app/utils/origin_badge";

describe("entintados_pdv: origin_badge", () => {
    test("nombre y porcentaje sin ceros sobrantes", () => {
        expect(formatOriginBadge("Promo TK", 20)).toBe("Promo TK · 20%");
        expect(formatOriginBadge("Promo TK", 12.5)).toBe("Promo TK · 12.5%");
        expect(formatOriginBadge("Promo TK", 100)).toBe("Promo TK · 100%");
    });

    test("término fijo, sin porcentaje y sin nombre", () => {
        expect(formatOriginBadge("Promo TK", "Regalo")).toBe("Promo TK · Regalo");
        expect(formatOriginBadge("Manual")).toBe("Manual");
        expect(formatOriginBadge("", 7)).toBe("7%");
    });
});
