"""Pruebas ORM locales; el runner revierte todos los datos al terminar."""
import test_stock_return_credit_orm as runner


def checks(env):
    from odoo.tools.convert import convert_file
    product = env['product.product'].create({
        'name': 'Prueba margen y auditoría', 'default_code': 'QA-MARGIN',
        'list_price': 100, 'standard_price': 40, 'taxes_id': [(5, 0, 0)],
        'supplier_taxes_id': [(5, 0, 0)],
    })
    partner = env['res.partner'].create({'name': 'Proveedor prueba margen', 'is_purchase_contact': True})
    order = env['purchase.order'].create({'partner_id': partner.id})
    line = env['purchase.order.line'].create({
        'order_id': order.id, 'product_id': product.id, 'product_qty': 1,
        'price_unit': 120, 'tax_ids': [(5, 0, 0)],
    })
    assert line.sale_cost_warning and order.sale_cost_warning
    assert line._onchange_sale_cost_warning()['warning']
    line.discount = 20
    assert not line.sale_cost_warning
    line.write({'discount': 0, 'price_unit': 100})
    assert not line.sale_cost_warning, 'Igual precio no debe advertir'
    line.price_unit = 101
    assert order.sale_cost_warning
    tax = env['account.tax'].create({
        'name': 'QA impuesto incluido', 'amount': 16, 'amount_type': 'percent',
        'price_include_override': 'tax_included', 'type_tax_use': 'purchase',
    })
    line.write({'price_unit': 116, 'tax_ids': [(6, 0, tax.ids)]})
    assert not line.sale_cost_warning, 'El impuesto incluido no es costo'
    line.write({'price_unit': 120, 'tax_ids': [(5, 0, 0)]})
    larger_uom = env['uom.uom'].create({'name': 'QA paquete de 2', 'relative_uom_id': product.uom_id.id, 'relative_factor': 2})
    line.write({'product_uom_id': larger_uom.id, 'price_unit': 180})
    assert not line.sale_cost_warning, 'Debe comparar costo por unidad, no por paquete'
    line.write({'product_uom_id': product.uom_id.id, 'price_unit': 120})
    product.write({'standard_price': 50})
    product.product_tmpl_id.with_context(product_audit_origin=order.name).write({'list_price': 130, 'description_purchase': 'Prueba historial'})
    env.flush_all()
    messages = env['mail.message'].search([
        ('model', '=', 'product.template'), ('res_id', '=', product.product_tmpl_id.id),
        ('body', 'ilike', 'Producto actualizado'),
    ])
    text = '\n'.join(str(m.body) for m in messages)
    assert '40' in text and '50' in text and '130' in text and order.name in text
    assert all(m.author_id == env.user.partner_id for m in messages)
    assert 'Prueba historial' in text
    supplier = env['product.supplierinfo'].create({
        'partner_id': partner.id, 'product_tmpl_id': product.product_tmpl_id.id, 'price': 30,
    })
    supplier.price = 35
    supplier.unlink()
    # Recepción real a costo promedio: el costo automático debe conservar el documento y autor.
    category = env['product.category'].create({'name': 'QA costo promedio', 'property_cost_method': 'average'})
    product.categ_id = category
    product.is_storable = True
    line.price_unit = 150
    order.button_confirm()
    picking = order.picking_ids
    picking.move_ids.quantity = 1
    picking.move_ids.picked = True
    picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
    assert product.standard_price == 150
    cost_messages = env['mail.message'].search([
        ('model', '=', 'product.template'), ('res_id', '=', product.product_tmpl_id.id),
        ('body', 'ilike', 'Inventario / recepción:'),
    ])
    assert cost_messages and picking.name in str(cost_messages[0].body)
    assert cost_messages[0].author_id == env.user.partner_id
    convert_file(env, 'entintados_pdv', 'views/purchase_order_views.xml', {}, mode='update')
    return 'PASS: compra sobre venta, descuento, igualdad, banner/vista, costo y precio en historial con usuario y origen.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
