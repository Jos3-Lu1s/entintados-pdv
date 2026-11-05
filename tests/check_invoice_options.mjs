import fs from "node:fs";
import assert from "node:assert/strict";

const source = fs.readFileSync(new URL("../static/src/app/utils/invoice_options.js", import.meta.url), "utf8");
const { canSplitInvoice, setInvoiceSplitCount, initializePaymentInvoice } = await import(
    "data:text/javascript;base64," + Buffer.from(source).toString("base64"));
const makeOrder = (values = {}) => ({
    uiState: {}, lines: [], payment_ids: [], state: "draft", to_invoice: false,
    getPartner: () => ({ l10n_mx_edi_usage: "G03" }),
    setToInvoice(value) { this.to_invoice = value; }, ...values,
});
const order = makeOrder();
initializePaymentInvoice(order, true);
assert.equal(order.to_invoice, true);
assert.equal(order.l10n_mx_edi_usage, "G03");
assert.equal(order.l10n_mx_edi_cfdi_to_public, false);
order.to_invoice = false;
initializePaymentInvoice(order, true);
assert.equal(order.to_invoice, true, "Receipt/invoice stays enabled on payment screen remounts");
const saved = makeOrder({ l10n_mx_edi_usage: "G01", l10n_mx_edi_cfdi_to_public: true,
    invoice_split_count: 3, uiState: { invoice_split_count: 3 } });
initializePaymentInvoice(saved, true);
assert.equal(saved.l10n_mx_edi_usage, "G01");
assert.equal(saved.l10n_mx_edi_cfdi_to_public, true);
assert.equal(saved.invoice_split_count, 3);
assert.equal(setInvoiceSplitCount(saved, "4"), 4);
assert.equal(saved.uiState.invoice_split_count, 4);
assert.equal(saved.invoice_split_count, 4);
for (const values of [
    { pos_payment_flow: "credit" }, { pos_payment_flow: "ppd_partial" },
    { l10n_mx_edi_payment_policy: "PPD" }, { uiState: { creditInvoiceSetup: true } },
    { payment_ids: [{ amount: 10, payment_method_id: { type: "pay_later" } }] },
]) {
    const credit = makeOrder(values);
    assert.equal(canSplitInvoice(credit), false);
    assert.equal(setInvoiceSplitCount(credit, 5), 1);
}
for (const values of [
    { lines: [{ settled_invoice_id: { id: 1 } }] },
    { lines: [{ settled_order_id: { account_move: { id: 2 } } }] },
    { finalized: true }, { state: "paid" }, { state: "done" },
]) {
    const excluded = makeOrder(values);
    initializePaymentInvoice(excluded, true);
    assert.equal(excluded.to_invoice, false);
}
for (const value of ["", "bad", "0", "-5"]) {
    assert.equal(setInvoiceSplitCount(makeOrder(), value), 1);
}
console.log("PASS: invoice stays enabled, saved fiscal choices, ordinary split counts, credit/PPD restrictions, collections and finalized orders preserved.");
