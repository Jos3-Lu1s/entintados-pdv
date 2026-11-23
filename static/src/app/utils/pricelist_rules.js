/** @odoo-module **/

/** Regla «por Esquema»: se guarda como `3_global`, sin producto ni categoría. */
export const SCHEME_APPLIED_ON = "3_esquema";

/** Leyenda del precio cuando ninguna regla de la tarifa aplica. */
export const LIST_PRICE_LABEL = "Precio de venta del producto";

function recordId(value) {
    return value?.id ?? value ?? false;
}

function belongsTo(item, pricelist) {
    return recordId(item.pricelist_id) === pricelist.id;
}

function isSchemeRule(item) {
    return item.display_applied_on === SCHEME_APPLIED_ON;
}

/** `applied_on` de Ventas; la caja no lo carga, se deduce de la regla. */
function appliedOnRank(item) {
    if (isSchemeRule(item)) {
        return 3;
    }
    if (item.product_id) {
        return 0;
    }
    if (item.product_tmpl_id) {
        return 1;
    }
    return item.categ_id ? 2 : 3;
}

/** `complete_name` de la categoría, que es su orden en Ventas. */
function categoryPath(category) {
    const names = [];
    const seen = new Set();
    let current = category;
    while (current && typeof current === "object" && !seen.has(current.id)) {
        seen.add(current.id);
        names.unshift(current.name || "");
        current = current.parent_id;
    }
    return names.join(" / ");
}

/** Orden de Ventas: `applied_on, min_quantity desc, categ_id desc, id desc`. */
function compareRules(a, b) {
    const byAppliedOn = appliedOnRank(a) - appliedOnRank(b);
    if (byAppliedOn) {
        return byAppliedOn;
    }
    const byQuantity = (b.min_quantity || 0) - (a.min_quantity || 0);
    if (byQuantity) {
        return byQuantity;
    }
    if (a.categ_id && b.categ_id) {
        const byCategory = categoryPath(b.categ_id).localeCompare(categoryPath(a.categ_id));
        if (byCategory) {
            return byCategory;
        }
    }
    return (b.id || 0) - (a.id || 0);
}

/**
 * Reglas que pueden aplicar al producto. Con los modelos de la caja usa los mismos índices
 * que el core (`backLink` del producto y las reglas generales por categoría de la tarifa);
 * sin ellos, recorre `item_ids`.
 */
function candidateItems(pricelist, product, template, categoryIds) {
    const itemModel = pricelist.models?.["product.pricelist.item"];
    if (itemModel && typeof pricelist.getGeneralRulesIdsByCategories === "function") {
        const variantRules = product.product_tmpl_id
            ? product.backLink?.("<-product.pricelist.item.product_id") || []
            : [];
        const templateRules = template.backLink?.("<-product.pricelist.item.product_tmpl_id") || [];
        const generalRules = itemModel.readMany(pricelist.getGeneralRulesIdsByCategories([...categoryIds]));
        return [...variantRules, ...templateRules, ...generalRules];
    }
    return pricelist.item_ids || [];
}

function parentCategoryIds(template) {
    if (Array.isArray(template?.parentCategories)) {
        return template.parentCategories.map(recordId);
    }
    const ids = [];
    let category = template?.categ_id;
    while (category) {
        ids.push(recordId(category));
        category = category.parent_id;
    }
    return ids;
}

/**
 * Regla de la tarifa que aplica al producto, con las reglas «por Esquema» y la prioridad
 * de Ventas (`product.pricelist.item._is_applicable_for` y su `_order`).
 *
 * Candidatas: las de la variante, las de la plantilla, las generales (por categoría o
 * globales) y las de esquema cuyo esquema es el del producto y cuya línea está vacía o
 * es la del producto. Gana la primera con `min_quantity <= quantity`.
 *
 * @param {Object} pricelist - `product.pricelist`
 * @param {Object} product - `product.product` o `product.template`
 * @param {number} quantity - Cantidad de la línea
 * @returns {Object|null} La regla aplicable, o `null` si rige el precio de venta
 */
export function findPricelistRule(pricelist, product, quantity = 1) {
    if (!pricelist || !product) {
        return null;
    }
    const template = product.product_tmpl_id || product;
    const variantId = product.product_tmpl_id ? product.id : false;
    const templateId = template.id;
    const categoryIds = new Set(parentCategoryIds(template));
    const schemeId = recordId(template.scheme_id);
    const lineId = recordId(template.lines_product_id);

    const isCandidate = (item) => {
        if (isSchemeRule(item)) {
            if (!schemeId || recordId(item.esquema_id) !== schemeId) {
                return false;
            }
            return !item.linea_id || recordId(item.linea_id) === lineId;
        }
        if (item.product_id) {
            return Boolean(variantId) && recordId(item.product_id) === variantId;
        }
        if (item.product_tmpl_id) {
            return recordId(item.product_tmpl_id) === templateId;
        }
        if (item.categ_id) {
            return categoryIds.has(recordId(item.categ_id));
        }
        return true;
    };

    const rules = [...new Set(candidateItems(pricelist, product, template, categoryIds))]
        .filter((item) => belongsTo(item, pricelist))
        .filter((item) => !item.min_quantity || item.min_quantity <= quantity)
        .filter(isCandidate)
        .sort(compareRules);
    return rules[0] || null;
}

/**
 * Leyenda del origen del precio de tarifa, igual que `sale.order.line._set_price_origin`:
 * `Tarifa: <tarifa> — <regla>: <precio>`, o el precio de venta del producto sin regla.
 */
export function pricelistOriginLabel(pricelist, item) {
    if (!item) {
        return LIST_PRICE_LABEL;
    }
    return `Tarifa: ${pricelist?.name || ""} — ${item.name || ""}: ${item.price || ""}`;
}
