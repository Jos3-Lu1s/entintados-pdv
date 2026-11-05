import fs from "node:fs";
import assert from "node:assert/strict";

const path = new URL("../static/src/fields/pos_tint_grouped_lines.js", import.meta.url);
const source = fs.readFileSync(path, "utf8").replace(/^import .*;\r?\n/gm, "").replace("export class", "class");
const Renderer = new Function("registry", "X2ManyField", "x2ManyField", "ListRenderer", "_t",
    source + "\nreturn PosTintListRenderer;")(
    { category: () => ({ add() {} }) }, class {}, {}, class {},
    (message, name) => message.replace("%s", name));
const renderer = new Renderer();
const row = (id, key, name, role) => ({ id, data: {
    tint_group_key: key, tint_group_name: name, tint_line_role: role,
} });
const first = row(1, "tint-a", "Base + Rojo", "base");
const firstDose = row(2, "tint-a", "Base + Rojo", "colorant");
const second = row(3, "tint-b", "Base + Azul", "base");
const secondDose = row(4, "tint-b", "Base + Azul", "colorant");
const regular = row(5, false, false, "regular");
const list = { records: [firstDose, regular, second, first, secondDose] };
const original = [...list.records];
assert.deepEqual(renderer.tintRecords(list), [first, firstDose, second, secondDose, regular]);
assert.equal(renderer.tintHeader(first, list), "Entintado: Base + Rojo");
assert.equal(renderer.tintHeader(second, list), "Entintado: Base + Azul");
assert.equal(renderer.tintHeader(firstDose, list), "");
assert.equal(renderer.tintHeader(regular, list), "Otros productos");
assert.deepEqual(list.records, original);
assert.equal(renderer.tintHeader(regular, { records: [regular] }), "");
assert.equal(renderer.tintHeader(firstDose, { records: [firstDose] }), "Entintado: Base + Rojo");
console.log("PASS: distinct tint headers, components together, ordinary products separated, no source mutation, pagination.");
