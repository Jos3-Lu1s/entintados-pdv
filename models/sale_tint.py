import math
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# An RPC context cannot impersonate an internal recipe operation.
_TINT_OPERATION = object()
_TINT_SYNC_SEQUENCE = object()


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def write(self, vals):
        if 'order_line' in vals and self.env.context.get('_tint_sync_sequence') is not _TINT_SYNC_SEQUENCE:
            res = super(SaleOrder, self.with_context(_tint_sync_sequence=_TINT_SYNC_SEQUENCE)).write(vals)
            self.order_line._check_tint_sequence_integrity()
            return res
        return super().write(vals)

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
        base = self.env['product.product'].browse(snapshot.get('base_id')).exists()
        color = self._tint_color_description(snapshot)
        if base and color:
            return '%s + %s' % (base.display_name, color)
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
        """Use frozen color data first; tolerate older snapshots without color fields."""
        code = (snapshot.get('color_code') or '').strip()
        name = (snapshot.get('color_name') or '').strip()
        if (not code or not name) and snapshot.get('formula_id'):
            formula = self.env['tint.color.formula'].browse(snapshot['formula_id']).exists()
            if formula:
                code = code or formula.color_id.code or ''
                name = name or formula.color_id.name or ''
        if not name:
            name = (snapshot.get('name') or '').split(' — ', 1)[0].strip()
            match = re.match(r'^\[([^\]]+)\]\s*(.*)$', name)
            if match:
                code = code or match[1]
                name = match[2]
        if code and name.startswith('[%s] ' % code):
            name = name[len(code) + 3:]
        return ' '.join(filter(None, ['[%s]' % code if code else '', name]))

    @api.model
    def _setup_sale_tint_layout(self):
        """Flatten untouched quotations, keeping recipe and stock references on the base."""
        orders = self.search([('state', 'in', ['draft', 'sent', 'cancel']),
                              '|', ('order_line.tint_snapshot', '!=', False), ('order_line.tint_area', '!=', False)])
        for order in orders:
            self.env['res.company']._with_locked_records(order)
            order.invalidate_recordset(['order_line'])
            headers = order.order_line.filtered(
                lambda line: line.display_type and line.tint_snapshot and not line.tint_snapshot.get('is_section_layout'))
            for header in headers:
                order.invalidate_recordset(['order_line'])
                components = order.order_line.exists().filtered(lambda line: line.tint_section_id == header)
                if any(line.qty_invoiced or line.qty_delivered for line in components):
                    continue
                if components.move_ids.filtered(lambda move: move.state != 'cancel'):
                    continue
                snapshot = dict(header.tint_snapshot)
                base_line = components.filtered(lambda line: line.product_id.id == snapshot['base_id'])
                if len(base_line) != 1:
                    continue
                already_base_only = snapshot.get('base_only')
                if not already_base_only:
                    formula = self.env['tint.color.formula'].browse(snapshot.get('formula_id')).exists()
                    snapshot.update({
                        'base_only': True,
                        'color_name': formula.color_id.name if formula else snapshot['name'],
                        'color_code': formula.color_id.code if formula else '',
                        'size_name': formula.size_id.display_name if formula else '',
                    })
                price = base_line.price_unit if already_base_only else snapshot['unit_price']
                snapshot['unit_price'] = price
                base_line.with_context(_tint_operation=_TINT_OPERATION).write({
                    'tint_snapshot': snapshot, 'tint_section_id': False,
                    'tint_container_qty': header.tint_container_qty or base_line.product_uom_qty,
                    'name': base_line.name if already_base_only else order._tint_base_description(snapshot),
                    'price_unit': price, 'technical_price_unit': price,
                })
                (components - base_line).with_context(_tint_operation=_TINT_OPERATION).unlink()
                header.with_context(_tint_operation=_TINT_OPERATION).write({'tint_snapshot': False})
                header.with_context(_tint_operation=_TINT_OPERATION).unlink()
            order.invalidate_recordset(['order_line'])
            # Leave historical layouts untouched when they still have real linked components.
            if not order.order_line.filtered('tint_section_id'):
                order.order_line.filtered('tint_area').with_context(_tint_operation=_TINT_OPERATION).unlink()
            order.invalidate_recordset(['order_line'])
            order.order_line.invalidate_recordset(['parent_id'])
            # Update only descriptions of untouched quotations. Confirmed, invoiced
            # or delivered documents retain their original stored description.
            if order.state in ('draft', 'sent'):
                for line in order.order_line.filtered(lambda item: item.tint_snapshot and item.tint_snapshot.get('base_only')):
                    if line.qty_invoiced or line.qty_delivered or line.move_ids.filtered(lambda move: move.state != 'cancel'):
                        continue
                    description = order._tint_base_description(line.tint_snapshot)
                    if line.name != description:
                        line.with_context(_tint_operation=_TINT_OPERATION).write({'name': description})

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

    def _get_invoiceable_lines(self, final=False):
        lines = super()._get_invoiceable_lines(final=final)
        return lines.filtered(lambda l: not (
            (l.display_type == 'line_section' and l.tint_snapshot)
            or (l.product_id and l.product_id.tint_role == 'colorant' and l.tint_section_id)
        ))

    def _apply_tint(self, formula, base, quantity, section=None):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        self.invalidate_recordset(['state', 'order_line'])
        if self.state not in ('draft', 'sent') or self.locked:
            raise ValidationError(_('Solo se puede agregar un entintado en una cotización sin bloquear.'))
        if not math.isfinite(quantity) or quantity <= 0 or quantity != int(quantity):
            raise ValidationError(_('Indica un número entero de envases mayor que cero.'))
        lines = self.env['sale.order.line'].with_context(_tint_operation=_TINT_OPERATION)
        if section:
            raise ValidationError(_('Los entintados existentes no se pueden ajustar; solo se pueden agregar nuevos.'))
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
            'base_only': False, 'color_name': formula.color_id.name, 'color_code': formula.color_id.code,
            'size_name': formula.size_id.display_name,
            'is_section_layout': True,
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
        header_vals = {
            'order_id': self.id,
            'sequence': sequence,
            'display_type': 'line_section',
            'name': self._tint_display_title(snapshot),
            'tint_snapshot': snapshot,
            'tint_container_qty': quantity,
            'collapse_prices': False,
            'collapse_composition': True,
        }
        header_line = lines.create([header_vals])

        base_vals = {
            'order_id': self.id,
            'sequence': sequence + 1,
            'product_id': base.id,
            'name': base.display_name,
            'product_uom_qty': quantity,
            'product_uom_id': base.uom_id.id,
            'price_unit': snapshot['base_unit_price'],
            'discount': 0,
            'tint_section_id': header_line.id,
            'tint_container_qty': quantity,
            'price_origin': 'manual',
            'technical_price_unit': snapshot['base_unit_price'],
        }

        child_vals = [base_vals]
        for idx, dose in enumerate(formula.line_ids.sorted('sequence')):
            colorant = dose.colorant_id
            dose_price = self._tint_component_price(colorant, point)
            child_vals.append({
                'order_id': self.id,
                'sequence': sequence + 2 + idx,
                'product_id': colorant.id,
                'name': colorant.display_name,
                'product_uom_qty': dose.points * quantity,
                'product_uom_id': point.id,
                'price_unit': dose_price,
                'discount': 0,
                'tint_section_id': header_line.id,
                'tint_container_qty': quantity,
                'price_origin': 'manual',
                'technical_price_unit': dose_price,
            })
        child_lines = lines.create(child_vals)
        base_line = child_lines.filtered(lambda l: l.product_id == base)
        return base_line.with_env(self.env)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    tint_area = fields.Selection([('tints', 'Entintados'), ('products', 'Otros productos')],
                                 string='Sección automática', copy=False, readonly=True)

    tint_section_id = fields.Many2one('sale.order.line', string='Encabezado del entintado',
                                    ondelete='cascade', copy=False, readonly=True, index=True)
    tint_snapshot = fields.Json(string='Fórmula cotizada', copy=False, readonly=True)
    tint_container_qty = fields.Float(string='Envases', copy=False, readonly=True)
    tint_display_name = fields.Char(string='Entintado', compute='_compute_tint_display_name')
    tint_color_description = fields.Char(string='Color del entintado', compute='_compute_tint_display_name')
    is_tint_section = fields.Boolean(string='Es sección de entintado', compute='_compute_is_tint_section')

    @api.depends('display_type', 'tint_snapshot')
    def _compute_is_tint_section(self):
        for line in self:
            line.is_tint_section = bool(line.display_type == 'line_section' and line.tint_snapshot)

    @api.depends('order_id.order_line.sequence', 'order_id.order_line.display_type')
    def _compute_parent_id(self):
        super()._compute_parent_id()
        for line in self:
            if line.parent_id and line.parent_id.tint_snapshot and line.tint_section_id != line.parent_id:
                line.parent_id = False

    @api.depends('tint_snapshot', 'tint_section_id.tint_snapshot', 'product_id', 'product_id.name', 'product_id.default_code')
    def _compute_tint_display_name(self):
        for line in self:
            recipe = line._tint_recipe()
            is_base = recipe and line.product_id.id == recipe.get('base_id') and not line.tint_section_id
            line.tint_display_name = line.product_id.display_name if is_base else False
            color = line.order_id._tint_color_description(recipe) if is_base else ''
            line.tint_color_description = '+ ' + color if color else False

    @api.depends('tint_section_id', 'product_id', 'product_id.tint_role')
    def _compute_qty_to_invoice(self):
        super()._compute_qty_to_invoice()
        for line in self:
            if line.tint_section_id and line.product_id and line.product_id.tint_role == 'colorant':
                line.qty_to_invoice = 0

    def _prepare_invoice_line(self, **optional_values):
        values = super()._prepare_invoice_line(**optional_values)
        recipe = self._tint_recipe()
        is_base = recipe and self.product_id.id == recipe.get('base_id')
        if is_base:
            values['name'] = self.order_id._tint_base_description(recipe).replace('\n', ' ')
            values['price_unit'] = recipe.get('unit_price', self.price_unit)
        return values

    def _tint_recipe(self):
        self.ensure_one()
        return self.tint_snapshot or self.tint_section_id.tint_snapshot or {}

    def _is_regular_product_line(self):
        return not self._tint_recipe() and super()._is_regular_product_line()

    def _is_manual_discount_allowed(self):
        return not self._tint_recipe() and super()._is_manual_discount_allowed()

    def _reset_price_unit(self):
        return super(SaleOrderLine, self.filtered(lambda line: not line._tint_recipe()))._reset_price_unit()

    def _tint_saved_component_price(self):
        self.ensure_one()
        snapshot = self._tint_recipe()
        if self.product_id.id == snapshot['base_id']:
            return snapshot['unit_price'] if snapshot.get('base_only') else snapshot.get('base_unit_price', snapshot['unit_price'])
        return next((dose.get('unit_price', 0) for dose in snapshot['doses']
                     if dose['product_id'] == self.product_id.id), 0)

    @api.depends('tint_snapshot', 'tint_section_id', 'tint_section_id.tint_snapshot')
    def _compute_price_unit(self):
        tinted = self.filtered(lambda line: line.product_id and line._tint_recipe())
        super(SaleOrderLine, self - tinted)._compute_price_unit()
        for line in tinted.with_context(_tint_operation=_TINT_OPERATION):
            line.price_unit = line._tint_saved_component_price()
            line.technical_price_unit = line.price_unit
            line.price_origin = 'manual'
            line.price_origin_label = _('Precio del entintado: base más colorantes')
            line.combo_price_snapshot = False
            line.combo_package_price = 0

    @api.depends('tint_snapshot', 'tint_section_id', 'tint_section_id.tint_snapshot')
    def _compute_discount(self):
        tinted = self.filtered(lambda line: line.product_id and line._tint_recipe())
        super(SaleOrderLine, self - tinted)._compute_discount()
        for line in tinted.with_context(_tint_operation=_TINT_OPERATION):
            line.discount = line._tint_recipe().get('pos_discount', 0)
            line.technical_discount = line.discount
            line.pricing_rule_type = 'none'
            line.pricing_rule_origin = False
            line.pricing_rule_id = False

    @api.constrains('sequence', 'order_id')
    def _check_tint_sequence_integrity(self):
        if self.env.context.get('_tint_sync_sequence') is _TINT_SYNC_SEQUENCE:
            return
        orders = self.mapped('order_id')
        for order in orders:
            tint_sections = order.order_line.filtered(lambda l: l.display_type == 'line_section' and l.tint_snapshot)
            for sec in tint_sections:
                children = order.order_line.filtered(lambda l: l.tint_section_id == sec)
                if not children:
                    continue
                sorted_children = children.sorted(lambda l: (0 if l.product_id.tint_role != 'colorant' else 1, l.sequence, l.id))
                expected_seq = sec.sequence + 1
                for child in sorted_children:
                    if child.sequence != expected_seq:
                        raise ValidationError(_('Las líneas de un entintado deben mantener secuencias consecutivas con su encabezado.'))
                    expected_seq += 1
                other_lines = order.order_line.filtered(lambda l: l != sec and l.tint_section_id != sec)
                colliding = other_lines.filtered(lambda l: sec.sequence <= l.sequence < expected_seq)
                if colliding:
                    raise ValidationError(_('No se pueden intercalar productos ajenos dentro de una sección de entintado.'))

    @api.model
    def _prepare_foreign_lines_sequence(self, vals_list):
        order_ids = set()
        for v in vals_list:
            oid = v.get('order_id') or self.env.context.get('default_order_id')
            if isinstance(oid, models.BaseModel):
                oid = oid.id
            if oid and not v.get('tint_section_id'):
                order_ids.add(oid)
        if not order_ids:
            return
        orders = self.env['sale.order'].browse(order_ids)
        order_next_seq = {}
        for order in orders:
            if order.order_line:
                order_next_seq[order.id] = max(order.order_line.mapped('sequence') or [0]) + 10
        for v in vals_list:
            oid = v.get('order_id') or self.env.context.get('default_order_id')
            if isinstance(oid, models.BaseModel):
                oid = oid.id
            if oid in order_next_seq and not v.get('tint_section_id'):
                if v.get('sequence') is None:
                    v['sequence'] = order_next_seq[oid]
                    order_next_seq[oid] += 10
                else:
                    order_next_seq[oid] = max(order_next_seq[oid], v['sequence'] + 10)

    @api.model
    def _validate_foreign_lines_sequence(self, vals_list):
        order_ids = {v['order_id'] for v in vals_list if v.get('order_id') and not v.get('tint_section_id')}
        if not order_ids:
            return
        orders = self.env['sale.order'].browse(order_ids)
        for order in orders:
            tint_sections = order.order_line.filtered(lambda l: l.display_type == 'line_section' and l.tint_snapshot)
            if not tint_sections:
                continue
            for sec in tint_sections:
                children = order.order_line.filtered(lambda l: l.tint_section_id == sec)
                if not children:
                    continue
                min_seq = sec.sequence
                max_seq = max(children.mapped('sequence'))
                for v in vals_list:
                    if v.get('order_id') == order.id and not v.get('tint_section_id'):
                        seq = v.get('sequence')
                        if seq is not None and min_seq <= seq <= max_seq:
                            raise ValidationError(_('No se pueden agregar productos ajenos dentro de una sección de entintado.'))

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('_tint_operation') is not _TINT_OPERATION:
            if any(v.get('tint_area') or v.get('tint_section_id') or v.get('tint_snapshot') or v.get('tint_container_qty') for v in vals_list):
                raise ValidationError(_('Agrega los entintados desde el selector de fórmulas.'))
            self._prepare_foreign_lines_sequence(vals_list)
            self._validate_foreign_lines_sequence(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        controlled = {'product_id', 'product_uom_id', 'product_uom_qty', 'price_unit', 'discount', 'name',
                      'tint_area', 'tint_section_id', 'tint_snapshot', 'tint_container_qty', 'order_id', 'display_type'}
        if self.env.context.get('_tint_operation') is not _TINT_OPERATION:
            if {'tint_area', 'tint_section_id', 'tint_snapshot', 'tint_container_qty'} & vals.keys() or (
                    controlled & vals.keys() and self.filtered(lambda l: l.tint_area or l.tint_snapshot or l.tint_section_id)):
                raise ValidationError(_('No se pueden modificar los envases ni los componentes de un entintado agregado.'))
            if 'sequence' in vals and self.filtered('tint_section_id') and self.env.context.get('_tint_sync_sequence') is not _TINT_SYNC_SEQUENCE:
                if any(vals['sequence'] != l.sequence for l in self.filtered('tint_section_id')):
                    raise ValidationError(_('No se pueden modificar los envases ni los componentes de un entintado agregado.'))
            if 'sequence' in vals and self.env.context.get('_tint_sync_sequence') is not _TINT_SYNC_SEQUENCE:
                target_seq = vals['sequence']
                regular_lines = self.filtered(lambda l: not l.tint_section_id and not (l.display_type == 'line_section' and l.tint_snapshot))
                if regular_lines:
                    for order in regular_lines.order_id:
                        tint_sections = order.order_line.filtered(lambda l: l.display_type == 'line_section' and l.tint_snapshot)
                        for sec in tint_sections:
                            children = order.order_line.filtered(lambda l: l.tint_section_id == sec)
                            if not children:
                                continue
                            if sec.sequence <= target_seq <= max(children.mapped('sequence')):
                                raise ValidationError(_('No se pueden mover productos dentro de una sección de entintado.'))
        res = super(SaleOrderLine, self.with_context(_tint_sync_sequence=_TINT_SYNC_SEQUENCE)).write(vals)
        if 'sequence' in vals:
            sections = self.filtered(lambda l: l.display_type == 'line_section' and l.tint_snapshot)
            for section in sections:
                children = section.order_id.order_line.filtered(lambda l: l.tint_section_id == section)
                sorted_children = children.sorted(lambda l: (0 if l.product_id.tint_role != 'colorant' else 1, l.sequence, l.id))
                for idx, child in enumerate(sorted_children, start=1):
                    target_seq = section.sequence + idx
                    if child.sequence != target_seq:
                        child.with_context(
                            _tint_operation=_TINT_OPERATION,
                            _tint_sync_sequence=_TINT_SYNC_SEQUENCE
                        ).write({'sequence': target_seq})
        if self.env.context.get('_tint_sync_sequence') is not _TINT_SYNC_SEQUENCE:
            (self | self.order_id.order_line.filtered('tint_section_id'))._check_tint_sequence_integrity()
        return res

    def unlink(self):
        if self.filtered('tint_area') and self.env.context.get('_tint_operation') is not _TINT_OPERATION:
            raise ValidationError(_('Las secciones Entintados y Otros productos se organizan automáticamente.'))
        flat = self.filtered(lambda line: line.product_id and line.tint_snapshot)
        if flat.filtered(lambda line: line.state not in ('draft', 'sent', 'cancel') or line.qty_invoiced or line.qty_delivered):
            raise ValidationError(_('No se puede eliminar una base entintada confirmada o con movimientos.'))
        sections = self.filtered(lambda line: line.display_type and line.tint_snapshot)
        child_sections = self.filtered('tint_section_id').mapped('tint_section_id')
        all_sections = sections | child_sections
        if all_sections.filtered(lambda l: l.order_id.state not in ('draft', 'sent', 'cancel')
                             or l.order_id.order_line.filtered(lambda c: c.tint_section_id == l and (c.qty_invoiced or c.qty_delivered))):
            raise ValidationError(_('No se puede eliminar un entintado confirmado o con movimientos.'))
        if all_sections and self.env.context.get('_tint_operation') is not _TINT_OPERATION:
            children = all_sections.order_id.order_line.filtered(lambda l: l.tint_section_id in all_sections)
            full_group = (self | all_sections | children).exists()
            return super(SaleOrderLine, full_group.with_context(_tint_operation=_TINT_OPERATION)).unlink()
        return super().unlink()

    def _get_section_lines(self):
        if self.tint_area:
            return self.order_id.order_line.filtered(
                lambda line: line.product_id and not line.display_type
                and bool(line.tint_section_id) == (self.tint_area == 'tints'))
        if self.display_type and self.tint_snapshot:
            return self.order_id.order_line.filtered(lambda l: l.tint_section_id == self)
        return super()._get_section_lines()

    def copy_data(self, default=None):
        if self.filtered(lambda l: l.tint_section_id or l.tint_snapshot):
            raise ValidationError(_('Agrega el entintado desde sus fórmulas en la nueva cotización para actualizar su precio.'))
        return super().copy_data(default)
