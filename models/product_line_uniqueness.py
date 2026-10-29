from odoo import _, api, models
from odoo.exceptions import ValidationError


class SaleOrderLineUniqueProduct(models.Model):
    _inherit = 'sale.order.line'

    def _is_regular_product_line(self):
        self.ensure_one()
        # Dos paquetes del mismo combo pueden llevar componentes distintos. Además, si el onchange
        # vaciara el paquete, el configurador de combos del core se abriría sin plantilla y fallaría.
        return bool(self.product_id and not self.display_type and not self.is_downpayment
                    and not self.is_reward_line and not self.combo_item_id
                    and self.product_id.type != 'combo'
                    and self.product_id != self.company_id.sale_discount_product_id)

    @api.constrains('order_id', 'product_id', 'display_type', 'is_downpayment', 'reward_id', 'combo_item_id')
    def _check_unique_sale_product(self):
        for order in self.order_id:
            seen = set()
            for line in order.order_line.filtered(lambda l: l._is_regular_product_line()):
                if line.product_id.id in seen:
                    raise ValidationError(_(
                        'El producto «%s» ya está en esta venta. Modifica la cantidad de la línea existente. '
                        'Solo se permiten líneas separadas para variantes distintas.') % line.product_id.display_name)
                seen.add(line.product_id.id)

    @api.model_create_multi
    def create(self, vals_list):
        ids = {v.get('order_id', self.env.context.get('default_order_id')) for v in vals_list}
        orders = self.env['sale.order'].browse(sorted(i for i in ids if i))
        if orders:
            self.env['res.company']._with_locked_records(orders)
            orders.invalidate_recordset(['order_line'])
        return super().create(vals_list)

    def write(self, vals):
        if {'order_id', 'product_id', 'display_type', 'is_downpayment', 'reward_id', 'combo_item_id'} & vals.keys():
            orders = self.order_id | self.env['sale.order'].browse(vals.get('order_id', []))
            if orders:
                self.env['res.company']._with_locked_records(orders.sorted('id'))
                orders.invalidate_recordset(['order_line'])
        return super().write(vals)

    @api.onchange('product_id')
    def _onchange_unique_sale_product(self):
        for line in self:
            if line._is_regular_product_line() and any(
                    other != line and other._is_regular_product_line() and other.product_id == line.product_id
                    for other in line.order_id.order_line):
                product = line.product_id.display_name
                line.product_id = False
                return {'warning': {'title': _('Producto repetido'), 'message': _(
                    'El producto «%s» ya está en la venta. Aumenta la cantidad en su línea existente '
                    'o selecciona una variante distinta.') % product}}


class CrmMaterialUniqueProduct(models.Model):
    _inherit = 'crm.material.line'

    def write(self, vals):
        if {'lead_id', 'product_id'} & vals.keys():
            leads = self.lead_id | self.env['crm.lead'].browse(vals.get('lead_id', []))
            if leads:
                self.env['res.company']._with_locked_records(leads.sorted('id'))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        ids = {v.get('lead_id', self.env.context.get('default_lead_id')) for v in vals_list}
        leads = self.env['crm.lead'].browse(sorted(i for i in ids if i))
        leads.check_access('write')
        if leads:
            self.env['res.company']._with_locked_records(leads)
        result = []
        for vals in vals_list:
            lead_id = vals.get('lead_id', self.env.context.get('default_lead_id'))
            lead = self.env['crm.lead'].browse(lead_id)
            if lead and lead._has_material_output():
                raise ValidationError(_('No se pueden agregar líneas de material una vez generada la salida de inventario.'))
            existing = self.search([('lead_id', '=', lead_id), ('product_id', '=', vals.get('product_id'))], limit=1)
            if existing:
                product = existing.product_id
                source_uom = self.env['uom.uom'].browse(vals.get('uom_id')) or product.uom_id
                quantity = source_uom._compute_quantity(
                    vals.get('quantity', 1), existing.uom_id or product.uom_id, round=False)
                changes = {'quantity': existing.quantity + quantity}
                description = vals.get('description')
                if description and description != existing.description:
                    changes['description'] = '\n'.join(filter(None, [existing.description, description]))
                existing.write(changes)
                result.append(existing.id)
            else:
                result.append(super().create([vals]).id)
        return self.browse(result)

    @api.constrains('lead_id', 'product_id')
    def _check_unique_demo_product(self):
        for line in self:
            if self.search_count([('lead_id', '=', line.lead_id.id), ('product_id', '=', line.product_id.id),
                                  ('id', '!=', line.id)], limit=1):
                raise ValidationError(_('El producto ya está en la demostración. Modifica la cantidad en su línea existente.'))
