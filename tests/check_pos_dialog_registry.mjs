import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
class Registry {
    content = new Map(); categories = new Map();
    category(name) {
        if (!this.categories.has(name)) this.categories.set(name, new Registry());
        return this.categories.get(name);
    }
    contains(key) { return this.content.has(key); }
    add(key, value, options = {}) {
        if (this.contains(key) && !options.force) throw new Error('duplicate');
        this.content.set(key, value); return this;
    }
}
const source = fs.readFileSync(new URL('../static/src/app/overrides/patch_registry.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?$/gm, '');
for (const preload of [true, false]) {
    const registry = new Registry();
    if (preload) registry.category('dialogs').add('custom_select_create', 'core');
    vm.runInNewContext(source, {registry});
    const dialogs = registry.category('dialogs');
    if (!preload) dialogs.add('custom_select_create', 'core');
    assert.equal(dialogs.add('custom_select_create', 'settle'), dialogs);
    assert.equal(dialogs.content.get('custom_select_create'), 'settle');
    dialogs.add('other', 1);
    assert.throws(()=>dialogs.add('other', 2), /duplicate/);
    registry.category('fields').add('custom_select_create', 1);
    assert.throws(()=>registry.category('fields').add('custom_select_create', 2), /duplicate/);
}
console.log('PASS: both dialog load orders; unrelated duplicates still rejected');
