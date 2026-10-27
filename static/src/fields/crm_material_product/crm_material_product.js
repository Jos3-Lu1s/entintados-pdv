import { registry } from '@web/core/registry';
import { toRaw } from '@odoo/owl';
import { Many2OneField, buildM2OFieldDescription } from '@web/views/fields/many2one/many2one_field';
import { mergeMaterialProduct } from '@entintados_pdv/fields/crm_material_product/merge_material_product';

export class CrmMaterialProduct extends Many2OneField {
    get m2oProps() {
        const props = super.m2oProps;
        const list = this.props.record._parentRecord?.data?.material_line_ids;
        return { ...props, update: (value, options) =>
            mergeMaterialProduct(this.props.record, value, props.update, options, list && toRaw(list)) };
    }
}

registry.category('fields').add('crm_material_product', buildM2OFieldDescription(CrmMaterialProduct));
