/**
 * Reglas de las promociones por línea y de pedido en la caja, con los mismos criterios que
 * Ventas (`sale.order` / `sale.order.line` en `models/sale_order*.py`).
 *
 * Funciones puras: leen el pedido y sus líneas como los modelos del PDV y no escriben nada.
 */

import { _t } from "@web/core/l10n/translation";
import { isComboItem } from "@entintados_pdv/app/utils/combo_rules";
import { agreementForLine } from "@entintados_pdv/app/utils/pricing_rules";

const { DateTime } = luxon;

export const LINE_PROMOTION_PROGRAM_TYPE = "promotion";
const LINE_PROMOTION_APPLICABILITIES = ["order", "specific"];

const idOf = (record) => record?.id ?? record ?? false;

/** Recompensa que se elige por línea y vive en el `discount` de la línea, como `_is_line_promotion`. */
export function isLinePromotionReward(reward) {
    return Boolean(
        reward &&
            reward.reward_type === "discount" &&
            reward.discount_mode === "percent" &&
            LINE_PROMOTION_APPLICABILITIES.includes(reward.discount_applicability) &&
            !reward.discount_max_amount &&
            reward.program_id?.program_type === LINE_PROMOTION_PROGRAM_TYPE
    );
}

function orderLines(order) {
    return order.getOrderlines ? order.getOrderlines() : order.lines || [];
}

export function isColorant(product) {
    return (product?.tint_role || product?.product_tmpl_id?.tint_role) === "colorant";
}

export function isAdvanceLine(line) {
    const product = line.product_id;
    if (product?.is_accounting_advance || product?.product_tmpl_id?.is_accounting_advance) {
        return true;
    }
    if (line.advance_source_ref) {
        return true;
    }
    const config = line.order_id?.config_id;
    return Boolean(
        config?.advance_risk_enabled &&
            product &&
            idOf(product) === idOf(config.down_payment_product_id)
    );
}

/** Línea que puede llevar una promoción por línea: `_admits_line_promotion` y su par del PDV. */
export function admitsLinePromotion(line) {
    if (!line?.product_id || !(Number(line.qty) > 0) || line.refunded_orderline_id) {
        return false;
    }
    return !(
        isColorant(line.product_id) ||
        line.combo_parent_id ||
        line.is_reward_line ||
        line.reward_id ||
        line.pricing_rule_type === "fixed_price" ||
        line.fixed_price_locked ||
        isAdvanceLine(line)
    );
}

/**
 * Importe sin IVA y con IVA con el que la línea cuenta para el mínimo de un programa.
 * La línea en promoción se mide sin ese descuento: aplicarla no puede cancelarla.
 */
export function lineThresholdAmounts(line) {
    const withoutDiscount = Boolean(line.promo_reward_id);
    let untaxed = 0;
    let taxed = 0;
    for (const component of [line, ...(line.combo_line_ids || [])]) {
        if (withoutDiscount) {
            untaxed += component.priceExclNoDiscount || 0;
            taxed += component.priceInclNoDiscount || 0;
        } else {
            untaxed += component.prices?.total_excluded || 0;
            taxed += component.prices?.total_included || 0;
        }
    }
    return { untaxed, taxed };
}

function roundDown2(value) {
    return Math.floor(value * 100 + 1e-9) / 100;
}

function ruleMatches(rule, product) {
    return Boolean(rule.any_product || (product && rule.validProductIds?.has(product.id)));
}

/**
 * Puntos que el pedido genera en `program`, o `null` si no cumple ninguna de sus reglas.
 * Como `_get_line_promotion_points`: el mínimo se mide sin el descuento de la propia
 * promoción pero con el del acuerdo.
 */
export function programPoints(order, program) {
    const lines = orderLines(order).filter((line) => !line.combo_parent_id);
    const qtyLines = lines.filter((line) => line.product_id && !line.reward_id);
    const activatedRules = order.uiState?.codeActivatedProgramRules || [];
    let points = null;
    for (const rule of program.rule_ids || []) {
        if (rule.mode === "with_code" && !activatedRules.includes(rule.id)) {
            continue;
        }
        // Las recompensas automáticas y las del propio programa no cuentan para su mínimo.
        const amountLines = lines.filter((line) => {
            if (!ruleMatches(rule, line.product_id)) {
                return false;
            }
            const reward = line.reward_id;
            return !(
                reward?.reward_type === "discount" &&
                (reward.program_id?.trigger === "auto" || idOf(reward.program_id) === program.id)
            );
        });
        let untaxed = 0;
        let taxed = 0;
        for (const line of amountLines) {
            const amounts = lineThresholdAmounts(line);
            untaxed += amounts.untaxed;
            taxed += amounts.taxed;
        }
        const reached = rule.minimum_amount_tax_mode === "incl" ? taxed : untaxed;
        if ((rule.minimum_amount || 0) > reached + 1e-9) {
            continue;
        }
        const qty = qtyLines
            .filter((line) => ruleMatches(rule, line.product_id))
            .reduce((sum, line) => sum + Number(line.qty || 0), 0);
        if (qty < (rule.minimum_qty || 0)) {
            continue;
        }
        points = points || 0;
        if (rule.reward_point_mode === "order") {
            points += rule.reward_point_amount || 0;
        } else if (rule.reward_point_mode === "unit") {
            points += (rule.reward_point_amount || 0) * qty;
        } else if (rule.reward_point_mode === "money") {
            points += roundDown2((rule.reward_point_amount || 0) * taxed);
        }
    }
    return points;
}

/** Programa vigente para el pedido: fechas, tarifa, cliente nominativo y reglas activables. */
export function isProgramCurrent(order, program, now = DateTime.now()) {
    if (!program || program.active === false) {
        return false;
    }
    const activatedRules = order.uiState?.codeActivatedProgramRules || [];
    const rules = program.rule_ids || [];
    if (
        program.trigger === "auto" &&
        !rules.some((rule) => rule.mode === "auto" || activatedRules.includes(rule.id))
    ) {
        return false;
    }
    if (program.trigger === "with_code" && !rules.some((rule) => activatedRules.includes(rule.id))) {
        return false;
    }
    if (program.is_nominative && !order.getPartner?.()) {
        return false;
    }
    if (program.date_from && program.date_from.startOf("day") > now) {
        return false;
    }
    if (program.date_to && program.date_to.endOf("day") < now) {
        return false;
    }
    const pricelists = program.pricelist_ids || [];
    if (pricelists.length && !pricelists.some((pricelist) => idOf(pricelist) === idOf(order.pricelist_id))) {
        return false;
    }
    return true;
}

function appliedProgramIds(order) {
    const ids = new Set();
    for (const line of orderLines(order)) {
        for (const reward of [line.promo_reward_id, line.reward_id]) {
            if (reward?.program_id) {
                ids.add(idOf(reward.program_id));
            }
        }
    }
    if (order.order_promo_reward_id?.program_id) {
        ids.add(idOf(order.order_promo_reward_id.program_id));
    }
    return ids;
}

/** Mayor % primero y, en empate, el menor id: el orden en que se elige y se ofrece. */
export function sortByDiscount(rewards) {
    return [...rewards].sort((a, b) => (b.discount || 0) - (a.discount || 0) || a.id - b.id);
}

/**
 * Recompensas por línea cuyo programa cumple sus condiciones en el pedido, como
 * `_get_available_promotion_rewards`. `required_points` se exige pero no se consume.
 */
export function availablePromotionRewards(order, { programs = null, now = DateTime.now() } = {}) {
    const allPrograms = programs || order.models?.["loyalty.program"]?.getAll?.() || [];
    const applied = appliedProgramIds(order);
    const rewards = [];
    for (const program of allPrograms) {
        if (program.program_type !== LINE_PROMOTION_PROGRAM_TYPE || !isProgramCurrent(order, program, now)) {
            continue;
        }
        if (
            program.limit_usage &&
            program.total_order_count >= program.max_usage &&
            !applied.has(program.id)
        ) {
            continue;
        }
        const points = programPoints(order, program);
        if (points === null) {
            continue;
        }
        for (const reward of program.reward_ids || []) {
            if (isLinePromotionReward(reward) && points >= (reward.required_points || 0)) {
                rewards.push(reward);
            }
        }
    }
    return rewards;
}

function rewardCoversProduct(reward, product) {
    return (reward.all_discount_product_ids || []).some((item) => idOf(item) === idOf(product));
}

/**
 * Recompensas que cada línea puede elegir, como `_get_line_promotion_rewards`.
 * Una `specific` solo vale para los productos de su dominio (`all_discount_product_ids`).
 */
export function lineRewards(order, options = {}) {
    const result = new Map(orderLines(order).map((line) => [line, []]));
    const eligible = orderLines(order).filter(admitsLinePromotion);
    if (!eligible.length) {
        return result;
    }
    let rewards = availablePromotionRewards(order, options);
    if (order.order_promo_reward_id) {
        // La promoción de pedido es de todo o nada: no convive con la de un producto.
        rewards = rewards.filter((reward) => reward.discount_applicability === "order");
    }
    for (const reward of rewards) {
        for (const line of eligible) {
            if (reward.discount_applicability === "order" || rewardCoversProduct(reward, line.product_id)) {
                result.get(line).push(reward);
            }
        }
    }
    return result;
}

/**
 * Decisión de «mejores promociones», como `_apply_best_line_promotions`: la promoción de pedido
 * de mayor %; si no hay, la `specific` de mayor % de cada línea, solo si supera su descuento.
 */
export function bestPromotionPlan(order, options = {}) {
    const available = availablePromotionRewards(order, options);
    const [orderReward] = sortByDiscount(
        available.filter((reward) => reward.discount_applicability === "order")
    );
    if (orderReward) {
        return { orderReward };
    }
    const lines = new Map();
    for (const [line, rewards] of lineRewards(order, options)) {
        const [best] = sortByDiscount(
            rewards.filter((reward) => reward.discount_applicability === "specific")
        );
        if (
            best &&
            idOf(best) !== idOf(line.promo_reward_id) &&
            (best.discount || 0) > Number(line.discount || 0) + 1e-9
        ) {
            lines.set(line, best);
        }
    }
    return { lines };
}

/** Motivo por el que el pedido pierde `reward`, como `_get_order_promotion_loss_reason`. */
export function orderLossReason(order, reward, now = DateTime.now()) {
    if (!isProgramCurrent(order, reward.program_id, now) || reward.active === false) {
        return _t("ya no está vigente");
    }
    return _t("el pedido ya no cumple la condición");
}

/** Motivo por el que la línea pierde `reward`, como `_get_line_promotion_loss_reason`. */
export function lossReason(order, line, reward, now = DateTime.now()) {
    if (reward.active !== false && isProgramCurrent(order, reward.program_id, now)) {
        if (!admitsLinePromotion(line)) {
            return _t("la línea ya no la admite");
        }
        if (reward.discount_applicability === "specific" && !rewardCoversProduct(reward, line.product_id)) {
            return _t("el producto ya no la admite");
        }
    }
    return orderLossReason(order, reward, now);
}

function promotionOption(reward, difference, applied) {
    return {
        reward_id: reward.id,
        name: reward.program_id?.name || "",
        target: reward.reward_target_label || "",
        discount: reward.discount,
        difference,
        applied,
        scope: reward.discount_applicability,
    };
}

/** La línea y sus hijas: el paquete de un combo vale 0 y su importe es el de sus productos. */
function linesWithPrice(line) {
    return [line, ...(line.combo_line_ids || [])];
}

/**
 * Lo que la estrella de la línea ofrece, con la forma de `promo_available_info` de Ventas
 * (`_compute_promo_available`): una opción por promoción con cuánto cambia el importe y, con
 * una aplicada, la vuelta al acuerdo vigente. `false` si la línea no tiene nada que ofrecer.
 *
 * @param {Object} order
 * @param {Object} line
 * @param {Object} [options]
 * @param {Map} [options.rewardsByLine] - `lineRewards(order)` ya calculado.
 * @param {(order: Object, line: Object) => Object} [options.agreementFor] - Acuerdo vigente.
 */
export function linePromotionInfo(order, line, options = {}) {
    const { rewardsByLine = null, agreementFor = agreementForLine, ...rewardOptions } = options;
    if (isComboItem(line)) {
        // Un producto del combo solo informa la promoción de su paquete: se elige allá.
        const packageReward = line.combo_parent_id?.promo_reward_id;
        return packageReward
            ? { options: [promotionOption(packageReward, 0, true)], fallback: false, from_package: true }
            : false;
    }
    const applied = line.promo_reward_id || false;
    const offered = (rewardsByLine || lineRewards(order, rewardOptions)).get(line) || [];
    const rewards = offered.filter((reward) => idOf(reward) !== idOf(applied));
    if (!rewards.length && !applied) {
        return false;
    }
    const round = (value) => (order.currency?.round ? order.currency.round(value) : value);
    const priceLines = linesWithPrice(line);
    const baseAmount = priceLines.reduce((sum, item) => sum + (Number(item.priceExclNoDiscount) || 0), 0);
    const currentDiscount = Number(line.discount) || 0;
    const difference = (discount) => round((baseAmount * ((Number(discount) || 0) - currentDiscount)) / 100);

    const optionsList = sortByDiscount(applied ? [...rewards, applied] : rewards).map((reward) =>
        promotionOption(reward, difference(reward.discount), idOf(reward) === idOf(applied))
    );
    let fallback = false;
    if (applied) {
        const agreement = agreementFor(order, line);
        if (agreement.type === "fixed_price") {
            const current = priceLines.reduce((sum, item) => sum + (Number(item.priceExcl) || 0), 0);
            fallback = {
                label: agreement.originLabel || _t("Precio fijo"),
                difference: round(current - (Number(agreement.price) || 0) * (Number(line.qty) || 0)),
            };
        } else {
            fallback = {
                label: (agreement.type === "discount" && agreement.originLabel) || _t("sin acuerdo"),
                difference: difference(agreement.type === "discount" ? agreement.discount : 0),
            };
        }
        // Quitar la promoción de pedido de una línea la quita de todo el pedido.
        fallback.scope = idOf(applied) === idOf(order.order_promo_reward_id) ? "order" : "specific";
    }
    return { options: optionsList, fallback };
}

/**
 * Líneas con alguna promoción disponible distinta de la que ya tienen, como
 * `promo_available_line_count` de Ventas.
 */
export function promotionLineCount(order, options = {}) {
    let count = 0;
    for (const [line, rewards] of lineRewards(order, options)) {
        if (rewards.some((reward) => idOf(reward) !== idOf(line.promo_reward_id))) {
            count++;
        }
    }
    return count;
}
