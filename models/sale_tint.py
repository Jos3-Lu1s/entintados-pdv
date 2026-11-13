import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# An RPC context cannot impersonate an internal recipe operation.
_TINT_OPERATION = object()


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.constrains('currency_id', 'company_id')
    def _check_tint_currency(self):
        for order in self:
            if any(line.tint_snapshot['currency_id'] != order.currency_id.id
                   for line in order.order_line.filtered('tint_snapshot')):
                raise ValidationError(_('Elimina los entintados y vuelve a agregarlos al cambiar la moneda.'))

    def action_add_tint(self):
        self.ensure_one()
        self.check_access('write')
        if self.state not in ('draft', 'sent'):
            raise ValidationError(_('Los entintados se agregan antes de confirmar la venta.'))
        return {
            'type': 'ir.actions.act_window', 'name': _('Agregar entintado'),
            'res_model': 'sale.tint.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_order_id': self.id},
        }

    def _tint_component_price(self, product, uom=None):
        """Card price in the sale UoM/currency, with the normal fiscal-position mapping."""
        self.ensure_one()
        product = product.with_company(self.company_id)
        return product._get_tax_included_unit_price(
            self.company_id, self.currency_id, self.date_order.date(), 'sale',
            product_uom=uom or product.uom_id, product_price_unit=product.lst_price,
            product_currency=self.company_id.currency_id, fiscal_position=self.fiscal_position_id)

    def _tint_unit_price(self, formula, base):
        self.ensure_one()
        point = self.env.ref('entintados_pdv.uom_tint_point')
        base = base.with_company(self.company_id)
        price = base.lst_price + sum(
            dose.colorant_id.uom_id._compute_price(
                dose.colorant_id.with_company(self.company_id).lst_price, point) * dose.points
            for dose in formula.line_ids)
        return base._get_tax_included_unit_price(
            self.company_id, self.currency_id, self.date_order.date(), 'sale',
            product_price_unit=price, product_currency=self.company_id.currency_id,
            fiscal_position=self.fiscal_position_id)

    def _tint_display_title(self, snapshot):
        title = _('Entintado: %s') % snapshot.get('color_name', snapshot.get('name', ''))
        if snapshot.get('color_code'):
            title += ' [%s]' % snapshot['color_code']
        if snapshot.get('size_name'):
            title += ' — %s' % snapshot['size_name']
        return title

    def _tint_base_description(self, snapshot):
        self.ensure_one()
        base = self.env['product.product'].browse(snapshot.get('base_id')).exists()
        color = self._tint_color_description(snapshot)
        return '\n'.join(filter(None, [base.display_name if base else '', '+ ' + color if color else '']))

    def _tint_color_description(self, snapshot):
        code = (snapshot.get('color_code') or '').strip()
        name = (snapshot.get('color_name') or '').strip()
        if code and name.startswith('[%s] ' % code):
            name = name[len(code) + 3:]
        return ' '.join(filter(None, ['[%s]' % code if code else '', name]))

    def _check_stock_availability(self):
        result = super()._check_stock_availability()
        point = self.env.ref('entintados_pdv.uom_tint_point')
        for order in self:
            quantities = {}
            for line in order.order_line.filtered(lambda line: line.product_id and line._tint_recipe().get('base_only')):
                pending = max(line.product_uom_qty - line.qty_delivered, 0)
                for dose in line._tint_recipe()['doses']:
                    product = self.env['product.product'].browse(dose['product_id'])
                    quantities[product] = quantities.get(product, 0) + point._compute_quantity(
                        dose['points'] * pending, product.uom_id, round=False)
            for product, quantity in quantities.items():
                available = product.with_company(order.company_id).with_context(
                    warehouse_id=order.warehouse_id.id, warehouse=order.warehouse_id.id).free_qty
                if product.uom_id.compare(quantity, available) > 0:
                    raise UserError(_('No hay suficiente colorante para el entintado:\n%s') %
                                    order._stock_shortage_details(product, quantity, available))
        return result

    def _apply_tint(self, formula, base, quantity):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        self.invalidate_recordset(['state', 'order_line'])
        if self.state not in ('draft', 'sent') or self.locked:
            raise ValidationError(_('Solo se puede agregar un entintado en una cotización sin bloquear.'))
        if not math.isfinite(quantity) or quantity <= 0 or quantity != int(quantity):
            raise ValidationError(_('Indica un número entero de envases mayor que cero.'))
        lines = self.env['sale.order.line'].with_context(_tint_operation=_TINT_OPERATION)
        formula.check_access('read')
        base.check_access('read')
        if (not formula.active or not formula.color_id.active or not formula.line_ids
                or not formula.fits or not base.active or not base.sale_ok or not base.is_storable
                or base.tint_role != 'base' or base.tint_base_type_id != formula.base_type_id
                or base.tint_size_id != formula.size_id
                or (formula.line_scheme_id and base.lines_product_id != formula.line_scheme_id)
                or (base.company_id and base.company_id != self.company_id)):
            raise ValidationError(_('Selecciona una fórmula vigente y una base compatible con su línea y presentación.'))
        point = self.env.ref('entintados_pdv.uom_tint_point')
        snapshot = {
            'name': '%s — %s — %s' % (formula.color_id.display_name, base.display_name, formula.size_id.display_name),
            'formula_id': formula.id, 'currency_id': self.currency_id.id,
            'base_id': base.id, 'unit_price': self._tint_unit_price(formula, base),
            'base_unit_price': self._tint_component_price(base),
            'base_only': True, 'color_name': formula.color_id.name, 'color_code': formula.color_id.code,
            'size_name': formula.size_id.display_name,
            'doses': [],
        }
        for dose in formula.line_ids.sorted('sequence'):
            product = dose.colorant_id
            if (not product.active or product.tint_role != 'colorant' or not product.is_storable
                    or not product.uom_id._has_common_reference(point)
                    or (product.company_id and product.company_id != self.company_id)):
                raise ValidationError(_('Revisa el seguimiento de inventario, la unidad de medida y la compañía del colorante %s.') % product.display_name)
            snapshot['doses'].append({'product_id': product.id, 'points': dose.points,
                                      'name': product.display_name, 'unit_price': self._tint_component_price(product, point)})
        sequence = max(self.order_line.mapped('sequence') or [0]) + 10
        base = self.env['product.product'].browse(snapshot['base_id'])
        values = [{
            'order_id': self.id, 'sequence': sequence, 'product_id': base.id,
            'name': self._tint_base_description(snapshot), 'product_uom_qty': quantity,
            'product_uom_id': base.uom_id.id, 'price_unit': snapshot['unit_price'],
            'discount': 0, 'tint_snapshot': snapshot, 'tint_container_qty': quantity,
            'price_origin': 'tint', 'technical_price_unit': snapshot['unit_price'],
        }]
        return lines.create(values).with_env(self.env)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    tint_snapshot = fields.Json(string='Fórmula cotizada', copy=False, readonly=True)
    tint_container_qty = fields.Float(string='Envases', copy=False, readonly=True)
    tint_display_name = fields.Char(string='Entintado', compute='_compute_tint_display_name')
    tint_color_description = fields.Char(string='Color del entintado', compute='_compute_tint_display_name')

    @api.depends('tint_snapshot', 'product_id', 'product_id.name', 'product_id.default_code')
    def _compute_tint_display_name(self):
        for line in self:
            recipe = line._tint_recipe()
            is_base = recipe and line.product_id.id == recipe.get('base_id')
            line.tint_display_name = line.product_id.display_name if is_base else False
            color = line.order_id._tint_color_description(recipe) if is_base else ''
            line.tint_color_description = '+ ' + color if color else False

    def _prepare_invoice_line(self, **optional_values):
        values = super()._prepare_invoice_line(**optional_values)
        recipe = self._tint_recipe()
        if recipe.get('base_only') and self.product_id.id == recipe.get('base_id'):
            values['name'] = self.order_id._tint_base_description(recipe).replace('\n', ' ')
        return values

    def _tint_recipe(self):
        self.ensure_one()
        return self.tint_snapshot or {}

    def _is_regular_product_line(self):
        return not self._tint_recipe() and super()._is_regular_product_line()

    def _is_manual_discount_allowed(self):
        return not self._tint_recipe() and super()._is_manual_discount_allowed()

    def _reset_price_unit(self):
        return super(SaleOrderLine, self.filtered(lambda line: not line._tint_recipe()))._reset_price_unit()

    def _tint_saved_component_price(self):
        self.ensure_one()
        return self._tint_recipe()['unit_price']

    @api.depends('tint_snapshot')
    def _compute_price_unit(self):
        tinted = self.filtered(lambda line: line.product_id and line._tint_recipe())
        super(SaleOrderLine, self - tinted)._compute_price_unit()
        for line in tinted.with_context(_tint_operation=_TINT_OPERATION):
            line.price_unit = line._tint_saved_component_price()
            line.technical_price_unit = line.price_unit
            line.price_origin = 'tint'
            line.price_origin_label = _('Precio del entintado: base más colorantes')
            line.combo_price_snapshot = False
            line.combo_package_price = 0

    @api.depends('tint_snapshot')
    def _compute_discount(self):
        tinted = self.filtered(lambda line: line.product_id and line._tint_recipe())
        super(SaleOrderLine, self - tinted)._compute_discount()
        for line in tinted.with_context(_tint_operation=_TINT_OPERATION):
            line.discount = line._tint_recipe().get('pos_discount', 0)
            line.technical_discount = line.discount
            line.pricing_rule_type = 'none'
            line.pricing_rule_origin = False
            line.pricing_rule_id = False

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('_tint_operation') is not _TINT_OPERATION and any(
                v.get('tint_snapshot') or v.get('tint_container_qty') for v in vals_list):
            raise ValidationError(_('Agrega los entintados desde el selector de fórmulas.'))
        return super().create(vals_list)

    def write(self, vals):
        controlled = {'product_id', 'product_uom_id', 'product_uom_qty', 'price_unit', 'discount', 'name',
                      'tint_snapshot', 'tint_container_qty', 'order_id', 'display_type'}
        if self.env.context.get('_tint_operation') is not _TINT_OPERATION:
            if {'tint_snapshot', 'tint_container_qty'} & vals.keys() or (
                    controlled & vals.keys() and self.filtered('tint_snapshot')):
                raise ValidationError(_('No se pueden modificar los envases ni los componentes de un entintado agregado.'))
        return super().write(vals)

    def unlink(self):
        flat = self.filtered(lambda line: line.product_id and line.tint_snapshot)
        if flat.filtered(lambda line: line.state not in ('draft', 'sent', 'cancel') or line.qty_invoiced or line.qty_delivered):
            raise ValidationError(_('No se puede eliminar una base entintada confirmada o con movimientos.'))
        return super().unlink()

    def copy_data(self, default=None):
        if self.filtered('tint_snapshot'):
            raise ValidationError(_('Agrega el entintado desde sus fórmulas en la nueva cotización para actualizar su precio.'))
        return super().copy_data(default)
