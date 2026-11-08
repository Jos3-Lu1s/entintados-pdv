import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/src/fields/sale_tint/sale_tint_label.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');

function fixture(data) {
    class Field {
        get productName() { return '[211241] Bases revision tecnica'; }
        get label() { return 'Descripción habitual'; }
        get m2oProps() { return { value: this.base, canOpen: true }; }
    }
    vm.runInNewContext(source, { SaleOrderLineProductField: Field,
        patch(target, extension) {
            Object.setPrototypeOf(extension, Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target)));
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        } });
    const field = new Field();
    field.base = { id: 11, display_name: '[211241] Bases revision tecnica' };
    field.props = { record: { data } };
    return field;
}

test('keeps native base code/name and shows only color as description', () => {
    const field = fixture({ tint_display_name: '[211241] Bases revision tecnica',
        tint_color_description: '+ [OMD-0001] Azul Amuro Ray', name: 'Old verbose description with formula' });
    assert.equal(field.productName, '[211241] Bases revision tecnica');
    assert.equal(field.label, '+ [OMD-0001] Azul Amuro Ray');
    assert.equal(field.m2oProps.value, field.base);
    assert.equal(field.m2oProps.readonly, true);
});

test('regular products retain the native label and behavior', () => {
    const field = fixture({ tint_display_name: false });
    assert.equal(field.label, 'Descripción habitual');
    assert.equal(field.m2oProps.value, field.base);
    assert.equal(field.m2oProps.readonly, undefined);
});

test('section components retain native label and protect product selection', () => {
    const field = fixture({ tint_section_id: [1, 'Section'], tint_display_name: false });
    assert.equal(field.label, 'Descripción habitual');
    assert.equal(field.m2oProps.readonly, true);
});
