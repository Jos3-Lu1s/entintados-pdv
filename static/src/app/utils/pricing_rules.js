/** @odoo-module **/

/**
 * Resuelve la regla de precio fijo o descuento aplicable para un producto
 * en el POS conforme a la jerarquía acordada:
 *   1. Precio fijo por Producto (inviolable frente a promociones y descuentos)
 *   2. Descuento por Producto
 *   3. Descuento por Línea de producto
 *   4. Descuento por Esquema de producto
 *   5. Descuento Global del cliente (partner.discount)
 *   Fallback: sin descuento ni precio fijo especial.
 *
 * @param {Object} pos - Instancia del PosStore
 * @param {Object} partner - Registro del cliente actual
 * @param {Object} product - Registro de product.product
 * @returns {Object} { type: 'fixed_price'|'discount'|'none', price: number|null, discount: number, rule: Object|null }
 */
export function getPartnerPricingRule(pos, partner, product, options = {}) {
    if (!partner || !product) {
        return { type: "none", originType: "none", priceOrigin: "list", originLabel: "", price: null, discount: 0, rule: null };
    }
    const partnerId = partner?.id ?? partner;
    const productId = product?.id ?? product;

    const models = pos?.models || pos?.data?.models || pos;

    // Obtener plantilla y IDs de línea y esquema
    const tmpl = product.raw?.product_tmpl_id || product.product_tmpl_id || product;
    const lineId =
        tmpl?.lines_product_id?.id ??
        tmpl?.lines_product_id ??
        product.lines_product_id?.id ??
        product.lines_product_id;
    const lineRecord =
        lineId && models?.["lines.product"]
            ? models["lines.product"].get(lineId)
            : null;
    const lineName = lineRecord?.name || tmpl?.lines_product_id?.name || product.lines_product_id?.name || "";

    const schemeId =
        lineRecord?.scheme?.id ??
        lineRecord?.scheme ??
        tmpl?.scheme_id?.id ??
        tmpl?.scheme_id ??
        product.scheme_id?.id ??
        product.scheme_id;
    const schemeRecord =
        schemeId && models?.["tint.schema"]
            ? models["tint.schema"].get(schemeId)
            : null;
    const schemeName = schemeRecord?.name || tmpl?.scheme_id?.name || product.scheme_id?.name || "";

    const allRules = models?.["res.partner.discount.rule"]?.getAll?.() || [];
    const partnerRules = allRules.filter((r) => {
        const pId = r.partner_id?.id ?? r.partner_id;
        return pId === partnerId;
    });

    // 1. Precio Fijo por Producto (Prioridad 1)
    const fixedRule = partnerRules.find(
        (r) =>
            r.rule_type === "fixed_price" &&
            r.applied_on === "0_product" &&
            (r.product_id?.id ?? r.product_id) === productId
    );
    if (fixedRule) {
        let calculatedPrice = Number(fixedRule.fixed_price || 0);

        // Conversión de UdM si la UdM de venta difiere de la UdM base del producto
        const productUomId = product.uom_id?.id ?? product.uom_id;
        const productUom =
            productUomId && models?.["uom.uom"]
                ? models["uom.uom"].get(productUomId)
                : product.uom_id;
        const targetUomId = options?.uom?.id ?? options?.uom_id?.id ?? options?.uom ?? options?.uom_id ?? productUomId;
        const targetUom =
            targetUomId && models?.["uom.uom"]
                ? models["uom.uom"].get(targetUomId)
                : options?.uom || options?.uom_id;

        if (productUom && targetUom && (productUom.id ?? productUom) !== (targetUom.id ?? targetUom)) {
            const pCat = productUom.category_id?.id ?? productUom.category_id;
            const tCat = targetUom.category_id?.id ?? targetUom.category_id;
            if (pCat && tCat && pCat === tCat && productUom.factor && targetUom.factor) {
                calculatedPrice = (calculatedPrice * Number(productUom.factor)) / Number(targetUom.factor);
            }
        }

        // Conversión de moneda si la divisa del pedido difiere de la moneda de la compañía
        const targetCurrency = options?.currency || options?.currency_id || pos?.currency;
        const companyCurrency = pos?.company?.currency_id || pos?.company_id?.currency_id;
        if (targetCurrency && companyCurrency && (targetCurrency.id ?? targetCurrency) !== (companyCurrency.id ?? companyCurrency)) {
            const fromCurr = typeof companyCurrency === "object" ? companyCurrency : models?.["res.currency"]?.get?.(companyCurrency);
            const toCurr = typeof targetCurrency === "object" ? targetCurrency : models?.["res.currency"]?.get?.(targetCurrency);
            if (fromCurr?.rate && toCurr?.rate && Number(fromCurr.rate) > 0) {
                calculatedPrice = (calculatedPrice / Number(fromCurr.rate)) * Number(toCurr.rate);
            }
        }

        return {
            type: "fixed_price",
            originType: "fixed_price",
            priceOrigin: "agreement",
            originLabel: "Precio Fijo",
            price: calculatedPrice,
            discount: 0,
            rule: fixedRule,
        };
    }

    // 2. Descuento por Producto (Prioridad 2)
    const prodDiscountRule = partnerRules.find(
        (r) =>
            r.rule_type === "discount" &&
            r.applied_on === "0_product" &&
            (r.product_id?.id ?? r.product_id) === productId
    );
    if (prodDiscountRule) {
        const disc = Number(prodDiscountRule.discount || 0);
        return {
            type: "discount",
            originType: "product",
            priceOrigin: "agreement",
            originLabel: `Desc. Producto (${disc.toFixed(1)}%)`,
            price: null,
            discount: disc,
            rule: prodDiscountRule,
        };
    }

    // 3. Descuento por Línea de producto (Prioridad 3)
    if (lineId) {
        const lineRule = partnerRules.find(
            (r) =>
                r.rule_type === "discount" &&
                r.applied_on === "1_line" &&
                (r.line_id?.id ?? r.line_id) === lineId
        );
        if (lineRule) {
            const disc = Number(lineRule.discount || 0);
            const resolvedLineName = lineName || lineRule.line_id?.name || "";
            return {
                type: "discount",
                originType: "line",
                priceOrigin: "agreement",
                originLabel: `Desc. Línea: ${resolvedLineName} (${disc.toFixed(1)}%)`.trim(),
                price: null,
                discount: disc,
                rule: lineRule,
            };
        }
    }

    // 4. Descuento por Esquema (Prioridad 4)
    if (schemeId) {
        const schemeRule = partnerRules.find(
            (r) =>
                r.rule_type === "discount" &&
                r.applied_on === "2_scheme" &&
                (r.scheme_id?.id ?? r.scheme_id) === schemeId
        );
        if (schemeRule) {
            const disc = Number(schemeRule.discount || 0);
            const resolvedSchemeName = schemeName || schemeRule.scheme_id?.name || "";
            return {
                type: "discount",
                originType: "scheme",
                priceOrigin: "agreement",
                originLabel: `Desc. Esquema: ${resolvedSchemeName} (${disc.toFixed(1)}%)`.trim(),
                price: null,
                discount: disc,
                rule: schemeRule,
            };
        }
    }

    // 5. Descuento Global del Cliente (Prioridad 5)
    const globalDiscount = Number(partner.discount || 0) * 100;
    if (globalDiscount > 0) {
        return {
            type: "discount",
            originType: "global",
            priceOrigin: "agreement",
            originLabel: `Desc. Global Cliente (${globalDiscount.toFixed(1)}%)`,
            price: null,
            discount: globalDiscount,
            rule: null,
        };
    }

    return { type: "none", originType: "none", priceOrigin: "list", originLabel: "", price: null, discount: 0, rule: null };
}

/**
 * Busca si aplica una regla específica de la tarifa (product.pricelist.item)
 * para un producto y cantidad dados en el Punto de Venta.
 *
 * @param {Object} pricelist - Registro de product.pricelist
 * @param {Object} product - Registro de product.product
 * @param {number} quantity - Cantidad vendida
 * @param {Object} models - Modelos de PosStore (opcional)
 * @returns {Object|null} El item de tarifa aplicable, o null si aplica catálogo base.
 */
export function getPricelistItemRule(pricelist, product, quantity = 1, models = null) {
    if (!pricelist || !product) {
        return null;
    }
    const productId = product.id ?? product;
    const tmpl = product.product_tmpl_id || product.raw?.product_tmpl_id || product;
    const tmplId = tmpl?.id ?? tmpl;
    const parentCategories = tmpl?.parentCategories || product.parentCategories || [];

    // 1. Relaciones en memoria cargadas en el frontend de POS
    if (typeof product.backLink === "function" || typeof tmpl?.backLink === "function") {
        const productTmpl = tmpl || product;
        const tmplRules = (productTmpl.backLink?.("<-product.pricelist.item.product_tmpl_id") || [])
            .filter((rule) => (rule.pricelist_id?.id ?? rule.pricelist_id) === pricelist.id && !rule.product_id)
            .sort((a, b) => (b.min_quantity || 0) - (a.min_quantity || 0));
        const productRules = (product.backLink?.("<-product.pricelist.item.product_id") || [])
            .filter((rule) => (rule.pricelist_id?.id ?? rule.pricelist_id) === pricelist.id)
            .sort((a, b) => (b.min_quantity || 0) - (a.min_quantity || 0));

        const tmplRulesSet = new Set(tmplRules.map((r) => r.id));
        const productRulesSet = new Set(productRules.map((r) => r.id));
        const generalRulesIds = typeof pricelist.getGeneralRulesIdsByCategories === "function"
            ? pricelist.getGeneralRulesIdsByCategories(parentCategories)
            : [];

        const itemModel = models?.["product.pricelist.item"] || productTmpl.models?.["product.pricelist.item"];
        if (itemModel && typeof itemModel.readMany === "function") {
            const rules = itemModel.readMany([...productRulesSet, ...tmplRulesSet, ...generalRulesIds])
                .filter((r) => !r.min_quantity || r.min_quantity <= quantity);
            if (rules.length > 0) {
                return rules[0];
            }
        }
    }

    // 2. Búsqueda en los items asociados a la tarifa o en el registro general de items
    const allItems = pricelist.item_ids || (models?.["product.pricelist.item"]?.getAll?.() || []).filter(
        (item) => (item.pricelist_id?.id ?? item.pricelist_id) === pricelist.id
    );

    if (Array.isArray(allItems) && allItems.length > 0) {
        const matchingRules = allItems.filter((item) => {
            if (item.min_quantity && item.min_quantity > quantity) {
                return false;
            }
            const itemPId = item.product_id?.id ?? item.product_id;
            if (itemPId) {
                return itemPId === productId;
            }
            const itemTId = item.product_tmpl_id?.id ?? item.product_tmpl_id;
            if (itemTId) {
                return itemTId === tmplId;
            }
            const itemCId = item.categ_id?.id ?? item.categ_id;
            if (itemCId) {
                const pCatIds = Array.isArray(parentCategories)
                    ? parentCategories.map((c) => (c?.id ?? c))
                    : [];
                return pCatIds.includes(itemCId);
            }
            return true; // Regla global para cualquier producto
        });

        if (matchingRules.length > 0) {
            matchingRules.sort((a, b) => {
                const getPriority = (item) => {
                    if (item.product_id) return 3;
                    if (item.product_tmpl_id) return 2;
                    if (item.categ_id) return 1;
                    return 0;
                };
                const diffPrio = getPriority(b) - getPriority(a);
                if (diffPrio !== 0) return diffPrio;
                return (b.min_quantity || 0) - (a.min_quantity || 0);
            });
            return matchingRules[0];
        }
    }

    return null;
}

/**
 * Genera la etiqueta informativa de origen cuando el precio proviene de una tarifa.
 *
 * @param {Object} pricelist - Registro de la tarifa activa
 * @param {Object} itemRule - Item de la tarifa aplicado
 * @param {Object} product - Producto vendido
 * @param {number|string} finalPrice - Precio unitario resultante
 * @returns {string} Etiqueta descriptiva canónica
 */
export function formatPricelistOriginLabel(pricelist, itemRule, product, finalPrice) {
    const pricelistName = pricelist?.name || pricelist?.display_name || "Tarifa";
    if (!itemRule) {
        return "Precio de venta del producto";
    }
    const ruleName = itemRule.name || itemRule.display_name || product?.display_name || "";
    const priceStr = finalPrice !== undefined && finalPrice !== null ? String(finalPrice) : "";
    if (ruleName && ruleName.includes(":")) {
        return `Tarifa: ${pricelistName} — ${ruleName}`;
    }
    return priceStr
        ? `Tarifa: ${pricelistName} — ${ruleName}: ${priceStr}`
        : `Tarifa: ${pricelistName} — ${ruleName}`;
}

