import fs from "node:fs";
import assert from "node:assert/strict";

const read = (name) => fs.readFileSync(new URL(name, import.meta.url), "utf8");
const source = read("../static/src/app/utils/quotation_print.js");
const { printableQuotations } = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
const first = { id: 10, name: "S00010" };
const second = { id: 20, name: "S00020" };
assert.deepEqual(printableQuotations({ lines: [] }, first), [first]);
assert.deepEqual(printableQuotations({ lines: [
    { sale_order_origin_id: second }, { sale_order_line_id: { order_id: second } },
] }, first), [second]);
assert.deepEqual(printableQuotations({ lines: [
    { sale_order_origin_id: first }, { sale_order_origin_id: second },
] }, null), [first, second]);
assert.deepEqual(printableQuotations({ lines: [{}] }, null), []);
const collection = read("../static/src/app/overrides/pos_invoice_collection.js")
    .replace(/^import .*;\r?\n/gm, "");
const Validation = class {};
new Function("OrderPaymentValidation", "patch", "AlertDialog", "_t", "ask", collection)(
    Validation, (target, extension) => Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension)),
    class {}, (value) => value, async () => true);
assert.equal(new Validation().shouldDownloadInvoice(), false);
console.log("PASS: created/recovered quotations, deduplication and multiple documents; only automatic invoice download is disabled.");
