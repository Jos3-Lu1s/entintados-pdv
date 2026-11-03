"""Sales duplicate protection and consolidated CRM demo quantities; rollback."""
import logging
from unittest.mock import patch
import test_stock_return_credit_orm as runner


def checks(env):
    from odoo import Command
    from odoo.exceptions import ValidationError
    from odoo.tools.convert import convert_file
    logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
    partner = env['res.partner'].create({'name': 'Unique product customer', 'phone': '5551234567'})
    product, other = env['product.product'].create([
        {'name': name, 'is_storable': True} for name in ['Unique product A', 'Unique product B']])
    order = env['sale.order'].create({'partner_id': partner.id})
    Line = env['sale.order.line']
    def rejected(action):
        try:
            with env.cr.savepoint():
                action()
        except ValidationError:
            return
        raise AssertionError('Expected duplicate product rejection')
    def sale_line(item=product, **extra):
        return Line.create(dict({'order_id': order.id, 'product_id': item.id, 'product_uom_qty': 1}, **extra))
    first = sale_line()
    rejected(sale_line)
    second = sale_line(other)
    rejected(lambda: second.write({'product_id': product.id}))
    first.product_uom_qty = 5
    assert first.product_uom_qty == 5 and len(order.order_line) == 2
    rejected(lambda: env['sale.order'].create({'partner_id': partner.id, 'order_line': [
        Command.create({'product_id': product.id}), Command.create({'product_id': product.id})]}))
    deposit = sale_line(is_downpayment=True, price_unit=10)
    assert deposit and not deposit._is_regular_product_line()
    attribute = env['product.attribute'].create({'name': 'Future variants', 'create_variant': 'always'})
    values = env['product.attribute.value'].create([{'name': name, 'attribute_id': attribute.id} for name in ['A', 'B']])
    template = env['product.template'].create({'name': 'Same template distinct variants',
        'attribute_line_ids': [Command.create({'attribute_id': attribute.id, 'value_ids': [Command.set(values.ids)]})]})
    variants = template.product_variant_ids
    assert len(variants) == 2
    sale_line(variants[0]); sale_line(variants[1])
    rejected(lambda: sale_line(variants[0]))
    combo = env['product.combo'].create({'name': 'Unique combo choice', 'combo_item_ids': [
        Command.create({'product_id': product.id}), Command.create({'product_id': other.id})]})
    package = env['product.product'].create({'name': 'Unique combo package', 'type': 'combo',
                                             'combo_ids': [Command.set(combo.ids)]})
    for choice in (product, other):
        package_line = sale_line(package)
        sale_line(choice, combo_item_id=combo.combo_item_ids.filtered(lambda i: i.product_id == choice).id,
                  linked_line_id=package_line.id)
    draft_package = Line.new({'order_id': order.id, 'product_id': package.id})
    assert not draft_package._onchange_unique_sale_product() and draft_package.product_template_id
    draft = Line.new({'order_id': order.id, 'product_id': product.id})
    assert draft._onchange_unique_sale_product()['warning']
    assert not draft.product_id
    lead = env['crm.lead'].create({'name': 'Unique product demonstration'})
    Material = env['crm.material.line']
    base = Material.create({'lead_id': lead.id, 'product_id': product.id, 'quantity': 2, 'uom_id': product.uom_id.id})
    repeated = Material.create({'lead_id': lead.id, 'product_id': product.id})
    assert repeated == base and base.quantity == 3 and len(lead.material_line_ids) == 1
    pair = env['uom.uom'].create({'name': 'Demo pair', 'relative_uom_id': product.uom_id.id, 'relative_factor': 2})
    Material.create({'lead_id': lead.id, 'product_id': product.id, 'quantity': 1, 'uom_id': pair.id})
    assert base.quantity == 5
    result = Material.create([{'lead_id': lead.id, 'product_id': other.id},
                              {'lead_id': lead.id, 'product_id': other.id}])
    assert len(result) == 2 and result[0] == result[1] and result[0].quantity == 2
    rejected(lambda: result[0].write({'product_id': product.id}))
    for variant in variants:
        Material.create({'lead_id': lead.id, 'product_id': variant.id})
    assert len(lead.material_line_ids) == 4
    nested = env['crm.lead'].create({'name': 'Nested demo duplicate', 'material_line_ids': [
        Command.create({'product_id': product.id}), Command.create({'product_id': product.id})]})
    assert len(nested.material_line_ids) == 1 and nested.material_line_ids.quantity == 2
    with patch.object(type(lead), '_has_material_output', return_value=True):
        rejected(lambda: Material.create({'lead_id': lead.id, 'product_id': product.id}))
    assert base.quantity == 5
    convert_file(env, 'entintados_pdv', 'views/crm_lead_views.xml', {}, mode='update')
    bundle = env['ir.qweb']._get_asset_bundle('web.assets_web', css=False, js=True)
    content = bundle.js().raw.decode()
    assert '@entintados_pdv/fields/crm_material_product/crm_material_product' in content
    assert '@entintados_pdv/fields/crm_material_product/merge_material_product' in content
    return 'PASS: venta sin repetidos, variantes distintas, anticipos, ediciones/importaciones, CRM suma cantidades y convierte UdM, creación anidada, salida bloqueada, vista y assets.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
