import { describe, expect, test } from "@odoo/hoot";
import {
    comboPayloadFromSaleLines,
    currentRulesResetValues,
    isImportedSaleLine,
    isSaleComboItem,
    matchComboItemSaleLines,
    quotedTotal,
    snapshotDoses,
    tintColorantChildValues,
} from "@entintados_pdv/app/utils/sale_order_import";

// Datos planos con la forma de los modelos del PDV.

const colorantA = { id: 21, display_name: "Colorante A" };
const colorantB = { id: 22, display_name: "Colorante B" };

function models(products) {
    const byId = new Map(products.map((product) => [product.id, product]));
    return { "product.product": { get: (id) => byId.get(id) } };
}

describe("isImportedSaleLine", () => {
    test("línea de producto de Ventas", () => {
        expect(isImportedSaleLine({ sale_order_line_id: { id: 1, is_downpayment: false } })).toBe(true);
    });

    test("anticipo o deducción de Ventas", () => {
        expect(isImportedSaleLine({ sale_order_line_id: { id: 1, is_downpayment: true } })).toBe(false);
    });

    test("línea capturada en la caja o anticipo nativo sin línea de venta", () => {
        expect(isImportedSaleLine({ product_id: { id: 5 } })).toBe(false);
        expect(isImportedSaleLine({ sale_order_origin_id: { id: 42 } })).toBe(false);
        expect(isImportedSaleLine(null)).toBe(false);
    });
});

describe("currentRulesResetValues", () => {
    test("deja la línea sin precio manual, descuento, acuerdo ni promoción", () => {
        expect(currentRulesResetValues()).toEqual({
            price_type: "original",
            discount: 0,
            pricing_rule_type: "none",
            pricing_rule_origin: "",
            pricing_rule_id: false,
            promo_reward_id: false,
        });
    });

    test("cada llamada devuelve un objeto nuevo", () => {
        expect(currentRulesResetValues()).not.toBe(currentRulesResetValues());
    });
});

describe("snapshotDoses", () => {
    const snapshot = {
        doses: [
            { product_id: 21, points: 12, name: "Colorante A" },
            { product_id: 22, points: 0.5, name: "Colorante B" },
            { product_id: 23, points: 0, name: "Sin puntos" },
        ],
    };

    test("toma los puntos congelados con los productos de la caja", () => {
        const doses = snapshotDoses(models([colorantA, colorantB]), snapshot);
        expect(doses.map((dose) => [dose.colorant, dose.points])).toEqual([
            [colorantA, 12],
            [colorantB, 0.5],
        ]);
    });

    test("un colorante no cargado queda sin producto", () => {
        const doses = snapshotDoses(models([colorantA]), snapshot);
        expect(doses[1].colorant).toBe(null);
        expect(doses[1].colorantId).toBe(22);
    });

    test("sin fórmula no hay dosis", () => {
        expect(snapshotDoses(models([]), null)).toEqual([]);
        expect(snapshotDoses(models([]), { doses: [] })).toEqual([]);
    });
});

describe("quotedTotal", () => {
    test("prorratea el total de Ventas a la cantidad del ticket", () => {
        const lines = [
            { qty: 2, quotedSaleLine: { product_uom_qty: 4, price_total: 400 } },
            { qty: 1, quotedSaleLine: { product_uom_qty: 1, price_total: 116 } },
        ];
        expect(quotedTotal(lines)).toBe(316);
    });

    test("ignora líneas sin datos de Ventas o sin cantidad cotizada", () => {
        const lines = [
            { qty: 3 },
            { qty: 1, quotedSaleLine: { product_uom_qty: 0, price_total: 50 } },
            { qty: 1, quotedSaleLine: { product_uom_qty: 1, price_total: 80 } },
        ];
        expect(quotedTotal(lines)).toBe(80);
    });
});

// Paquete de 3 combos: «Mesa» incluye 1, «Silla» incluye 2. Por paquete se eligieron
// 1 mesa, 2 sillas incluidas y 1 silla extra.
const mesa = { id: 1, qty_free: 1 };
const silla = { id: 2, qty_free: 2 };
const mesaItem = { id: 11, combo_id: mesa };
const sillaItem = { id: 12, combo_id: silla };
const packageLine = { id: 50, product_uom_qty: 3 };

function itemLine(id, comboItem, qty, values = {}) {
    return { id, combo_item_id: comboItem, linked_line_id: { id: 50 }, product_uom_qty: qty, ...values };
}

describe("isSaleComboItem", () => {
    test("producto con paquete y opción", () => {
        expect(isSaleComboItem(itemLine(1, mesaItem, 3))).toBe(true);
    });

    test("paquete, línea normal o producto sin paquete", () => {
        expect(isSaleComboItem(packageLine)).toBe(false);
        expect(isSaleComboItem({ id: 2, product_uom_qty: 1 })).toBe(false);
        expect(isSaleComboItem({ id: 3, combo_item_id: mesaItem })).toBe(false);
        expect(isSaleComboItem(null)).toBe(false);
    });
});

describe("comboPayloadFromSaleLines", () => {
    const saleLines = [
        packageLine,
        itemLine(51, mesaItem, 3, { product_no_variant_attribute_value_ids: [{ id: 901 }, 902] }),
        itemLine(52, sillaItem, 3),
        itemLine(53, sillaItem, 3),
        itemLine(54, sillaItem, 3),
        { id: 60, product_uom_qty: 1 },
        { id: 70, combo_item_id: mesaItem, linked_line_id: { id: 99 }, product_uom_qty: 1 },
    ];

    test("cantidad por paquete y separación de incluidos y extras con qty_free", () => {
        const [included, extras] = comboPayloadFromSaleLines(packageLine, saleLines);
        expect(included.map((conf) => [conf.combo_item_id.id, conf.qty])).toEqual([
            [11, 1],
            [12, 1],
            [12, 1],
        ]);
        expect(extras.map((conf) => [conf.combo_item_id.id, conf.qty])).toEqual([[12, 1]]);
    });

    test("solo toma los productos de su paquete", () => {
        const [included, extras] = comboPayloadFromSaleLines(packageLine, saleLines);
        expect(included.length + extras.length).toBe(4);
    });

    test("pasa los atributos sin variante como ids", () => {
        const [included] = comboPayloadFromSaleLines(packageLine, saleLines);
        expect(included[0].configuration).toEqual({
            attribute_value_ids: [901, 902],
            attribute_custom_values: {},
        });
        expect(included[1].configuration.attribute_value_ids).toEqual([]);
    });

    test("paquete sin productos", () => {
        expect(comboPayloadFromSaleLines(packageLine, [packageLine])).toEqual([[], []]);
    });
});

describe("matchComboItemSaleLines", () => {
    test("empareja por combo_item_id en el orden de las líneas, con opciones repetidas", () => {
        const saleA = itemLine(52, sillaItem, 3);
        const saleB = itemLine(53, sillaItem, 3);
        const saleMesa = itemLine(51, mesaItem, 3);
        const posMesa = { combo_item_id: mesaItem };
        const posSillaA = { combo_item_id: sillaItem };
        const posSillaB = { combo_item_id: { id: 12 } };
        const matches = matchComboItemSaleLines([posMesa, posSillaA, posSillaB], [saleA, saleB, saleMesa]);
        expect(matches.get(posMesa)).toBe(saleMesa);
        expect(matches.get(posSillaA)).toBe(saleA);
        expect(matches.get(posSillaB)).toBe(saleB);
    });

    test("deja fuera lo que no empareja", () => {
        const posExtra = { combo_item_id: { id: 77 } };
        const matches = matchComboItemSaleLines([posExtra, { qty: 1 }], [itemLine(51, mesaItem, 3)]);
        expect(matches.size).toBe(0);
    });
});

describe("tintColorantChildValues", () => {
    test("una hija a $0 por dosis con puntos, con puntos × envases", () => {
        const doses = [
            { colorant: colorantA, points: 12 },
            { colorant: colorantB, points: 0 },
            { colorant: null, colorantId: 23, points: 4 },
        ];
        expect(tintColorantChildValues(doses, 2)).toEqual([
            {
                product_id: colorantA,
                qty: 24,
                price_unit: 0,
                price_type: "manual",
                unit_points: 12,
                is_tint_colorant: true,
            },
        ]);
    });

    test("sin dosis no hay hijas", () => {
        expect(tintColorantChildValues([], 3)).toEqual([]);
        expect(tintColorantChildValues(null, 3)).toEqual([]);
    });
});
