import math

from odoo import api, Command, fields, models, _
from odoo.exceptions import AccessError, UserError

from .sale_tint import _TINT_OPERATION

_QUOTATION_OPERATION = object()


class PosOrderQuotation(models.Model):
    _inherit = 'pos.order'

    generated_quotation_id = fields.Many2one('sale.order', string='Cotización creada', readonly=True,
                                             copy=False, check_company=True, ondelete='restrict')

    @api.model
    def _load_pos_data_fields(self, config):
        names = super()._load_pos_data_fields(config)
        # An empty native list means every accessible field. Do not load links
        # to accounting-only models for ordinary cashiers when cancelling a ticket.
        accessible = self.fields_get()
        names = names or list(accessible)
        return [name for name in names if name in accessible and (
            not self._fields[name].relational
            or self.env[self._fields[name].comodel_name].has_access('read'))]

    @api.model_create_multi
    def create(self, values_list):
        if any(values.get('generated_quotation_id') for values in values_list):
            raise UserError(_('La cotización se asigna al convertir el ticket.'))
        return super().create(values_list)

    def write(self, values):
        if 'generated_quotation_id' in values and self.env.context.get('_quotation_operation') is not _QUOTATION_OPERATION:
            if any(order.generated_quotation_id.id != (values['generated_quotation_id'] or False) for order in self):
                raise UserError(_('La cotización se asigna al convertir el ticket.'))
            # POS serialization also sends unchanged readonly fields, including False.
            values = dict(values)
            values.pop('generated_quotation_id')
        protected = {'state', 'lines', 'payment_ids', 'partner_id', 'pricelist_id', 'fiscal_position_id',
                     'session_id', 'amount_paid', 'amount_total', 'amount_tax', 'amount_return'}
        if (protected.intersection(values) and self.filtered('generated_quotation_id')
                and values != {'state': 'cancel'}):
            raise UserError(_('El ticket convertido ya no se modifica ni se cobra. Recupera su cotización.'))
        return super().write(values)

    def get_generated_sale_quotation(self, expected=None):
        self.ensure_one()
        self.check_access('read')
        quotation = self.generated_quotation_id
        if quotation:
            self._check_quotation_ticket_snapshot(expected)
            quotation.check_access('read')
            return {'id': quotation.id, 'name': quotation.name}
        return False

    @api.model
    def create_sale_quotation_from_ui(self, order, expected=None):
        """Persist the ticket and quotation together; retries use the POS UUID."""
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Se requieren permisos de Punto de Venta.'))
        if not order.get('uuid') or order.get('state') != 'draft':
            raise UserError(_('Solo se puede cotizar un ticket pendiente, sin cobrar.'))
        uuid = order['uuid']
        with self.env.cr.savepoint():
            ticket = self.search([('uuid', '=', uuid)], limit=1)
            if ticket:
                self.env['res.company']._with_locked_records(ticket)
                ticket.invalidate_recordset()
                quotation = ticket.get_generated_sale_quotation(expected)
                if quotation:
                    return quotation
            self.sync_from_ui([order])
            ticket = self.search([('uuid', '=', uuid)], limit=1)
            if not ticket:
                raise UserError(_('No se pudo guardar el ticket para crear la cotización.'))
            return ticket.action_create_sale_quotation(expected)

    @api.model
    def _process_order(self, order, existing_order):
        if existing_order:
            self.env['res.company']._with_locked_records(existing_order)
            existing_order.invalidate_recordset()
            if existing_order.generated_quotation_id:
                raise UserError(_('Este ticket ya se convirtió en la cotización %s. Recupérala desde Cotización/Orden.') % existing_order.generated_quotation_id.name)
        return super()._process_order(order, existing_order)

    def _check_quotation_ticket_snapshot(self, expected):
        if expected is None:
            return
        current = {'partner_id': self.partner_id.id, 'pricelist_id': self.pricelist_id.id,
                   'fiscal_position_id': self.fiscal_position_id.id}
        if any(expected.get(key) != value for key, value in current.items()):
            raise UserError(_('El cliente o la tarifa cambió. Sincroniza el ticket y vuelve a intentar.'))
        stored = {line.uuid: line for line in self.lines}
        sent = expected.get('lines', [])
        if len(sent) != len(stored) or {item[0] for item in sent if len(item) == 8} != set(stored):
            raise UserError(_('El ticket todavía está sincronizándose o cambió en otra caja. Espera y vuelve a intentar.'))
        for uuid, product, qty, price, discount, parent, note, color in sent:
            line = stored[uuid]
            if (product != line.product_id.id or parent != (line.combo_parent_id.uuid or False)
                    or note != (line.customer_note or '') or color != (line.tint_color_code or False)
                    or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                           or not math.isclose(value, saved, rel_tol=1e-9, abs_tol=1e-7)
                           for value, saved in [(qty, line.qty), (price, line.price_unit), (discount, line.discount)])):
                raise UserError(_('El ticket todavía está sincronizándose o cambió en otra caja. Espera y vuelve a intentar.'))

    def action_create_sale_quotation(self, expected=None):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_('Se requieren permisos de Punto de Venta.'))
        with self.env.cr.savepoint():
            self.env['res.company']._with_locked_records(self)
            self.invalidate_recordset()
            self._check_quotation_ticket_snapshot(expected)
            if self.generated_quotation_id:
                self.generated_quotation_id.check_access('read')
                return {'id': self.generated_quotation_id.id, 'name': self.generated_quotation_id.name}
            try:
                self.env['sale.order'].check_access('create')
            except AccessError:
                raise AccessError(_('Tu usuario necesita permiso para crear cotizaciones en Ventas, además del acceso a Punto de Venta.')) from None
            if (self.state != 'draft' or self.session_id.state != 'opened'
                    or self.company_id not in self.env.companies):
                raise UserError(_('Usa un ticket pendiente de una sesión abierta de la compañía activa.'))
            self.env['res.company']._with_locked_records(self.lines)
            self.lines.invalidate_recordset()
            if not self.partner_id or not self.lines:
                raise UserError(_('Selecciona un cliente y agrega productos antes de crear la cotización.'))
            if self.payment_ids or self.amount_paid or self.account_move or self.picking_ids:
                raise UserError(_('El ticket debe estar sin pagos, facturas ni salidas de inventario.'))
            if self.lines.filtered(lambda line: line.sale_order_line_id or line.sale_order_origin_id
                                   or line.refunded_orderline_id or line._is_accounting_advance_line()):
                raise UserError(_('No conviertas anticipos, devoluciones ni tickets que ya provienen de una orden de venta.'))
            self._check_colorant_lines()
            if any(not all(math.isfinite(value) for value in (line.qty, line.price_unit, line.discount))
                   or line.qty <= 0 or not 0 <= line.discount <= 100 for line in self.lines):
                raise UserError(_('Revisa las cantidades, precios y descuentos del ticket.'))
            if self.lines.filtered(lambda line: line.combo_parent_id and line.product_id.tint_role != 'colorant'):
                raise UserError(_('Este ticket contiene un combo comercial. Cotízalo desde Ventas antes de cobrarlo en caja.'))
            pricelist = self._quotation_pricelist()
            quotation = self.env['sale.order'].with_company(self.company_id).create({
                'state': 'draft',
                'partner_id': self.partner_id.id, 'company_id': self.company_id.id,
                'pricelist_id': pricelist.id, 'fiscal_position_id': self.fiscal_position_id.id,
                'warehouse_id': self.config_id.warehouse_id.id,
                'user_id': self.partner_id._contact_sale_owner().id,
                'team_id': self.config_id.crm_team_id.id, 'origin': self.pos_reference or self.name,
            })
            for sequence, line in enumerate(self.lines.filtered(lambda item: not item.combo_parent_id), 1):
                if not line.product_id.sale_ok or not line.product_id.active:
                    raise UserError(_('El producto %s no está habilitado para Ventas.') % line.product_id.display_name)
                if line.product_id.type == 'combo':
                    raise UserError(_('Los combos comerciales se cotizan desde Ventas.'))
                snapshot = self._quotation_tint_snapshot(line)
                description = (quotation._tint_base_description(snapshot) if snapshot
                               else '\n'.join(filter(None, [line.full_product_name or line.product_id.display_name,
                                                            line.customer_note])))
                values = {'order_id': quotation.id, 'sequence': sequence * 10,
                          'product_id': line.product_id.id, 'product_uom_id': line.product_id.uom_id.id,
                          'product_uom_qty': line.qty, 'price_unit': line.price_unit,
                          'technical_price_unit': line.price_unit, 'discount': line.discount,
                          'price_origin': 'manual', 'price_origin_label': _('Precio conservado del Punto de Venta'),
                          'tax_ids': [Command.set(line.tax_ids_after_fiscal_position.ids)], 'name': description}
                if snapshot:
                    values.update(tint_snapshot=snapshot, tint_container_qty=line.qty)
                self.env['sale.order.line'].with_context(_tint_operation=_TINT_OPERATION).create(values)
            if self.currency_id.compare_amounts(quotation.amount_total, self.amount_total):
                raise UserError(_('El total de la cotización difiere del ticket. Revisa impuestos, redondeos o promociones; no se convirtió el ticket.'))
            self.with_context(_quotation_operation=_QUOTATION_OPERATION).write({'generated_quotation_id': quotation.id})
            self.action_pos_order_cancel()
            quotation.message_post(body=_('Cotización creada desde el ticket %s, sin cobro ni salida de inventario.') % (self.pos_reference or self.name))
            return {'id': quotation.id, 'name': quotation.name}

    def _quotation_pricelist(self):
        self.ensure_one()
        if self.pricelist_id:
            if self.pricelist_id.currency_id != self.currency_id:
                raise UserError(_('La tarifa seleccionada en el ticket no coincide con la moneda de la caja.'))
            return self.pricelist_id
        candidates = (self.config_id.pricelist_id
                      | self.partner_id.with_company(self.company_id).property_product_pricelist)
        matching = candidates.filtered(lambda item: item.active and item.currency_id == self.currency_id
                                      and (not item.company_id or item.company_id == self.company_id))
        if matching:
            return matching[:1]
        # With pricelists disabled, POS sells at card prices in its own currency.
        # A matching sales pricelist supplies the quotation currency; line prices stay frozen.
        matching = self.env['product.pricelist'].search([
            ('currency_id', '=', self.currency_id.id),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not matching:
            # Creating this currency-only bridge does not grant pricelist administration
            # to the cashier, nor enable or change the POS pricing configuration.
            self.env['res.company']._with_locked_records(self.company_id)
            Pricelist = self.env['product.pricelist'].sudo().with_company(self.company_id)
            matching = Pricelist.search([
                ('currency_id', '=', self.currency_id.id),
                '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
            ], limit=1)
            if not matching:
                matching = Pricelist.create({
                    'name': _('Cotizaciones de Punto de Venta — %s') % self.currency_id.name,
                    'currency_id': self.currency_id.id, 'company_id': self.company_id.id,
                })
            matching = matching.with_env(self.env)
        return matching

    def _quotation_tint_snapshot(self, line):
        children = line.combo_line_ids
        if not children and not line.tint_color_code:
            return {}
        if (line.product_id.tint_role != 'base' or not children or line.qty != int(line.qty)
                or any(child.product_id.tint_role != 'colorant' or child.price_unit for child in children)):
            raise UserError(_('El entintado debe incluir una base y su fórmula completa de colorantes sin precio separado.'))
        point = self.env.ref('entintados_pdv.uom_tint_point')
        colors = self.env['tint.color'].search([('code', '=', line.tint_color_code)], limit=2) if line.tint_color_code else self.env['tint.color']
        color_name = colors.name if len(colors) == 1 else line.customer_note or line.tint_color_code or line.product_id.name
        snapshot = {'name': color_name, 'color_name': color_name, 'color_code': line.tint_color_code or '',
                    'size_name': line.product_id.tint_size_id.display_name, 'formula_id': False,
                    'base_id': line.product_id.id, 'base_only': True, 'currency_id': self.currency_id.id,
                    'unit_price': line.price_unit, 'pos_discount': line.discount, 'doses': []}
        for child in children:
            if not child.product_id.is_storable or not child.product_id.uom_id._has_common_reference(point):
                raise UserError(_('Revisa la unidad y el inventario del colorante %s.') % child.product_id.display_name)
            points = child.product_id.uom_id._compute_quantity(child.qty / line.qty, point, round=False)
            snapshot['doses'].append({'product_id': child.product_id.id, 'name': child.product_id.display_name,
                                      'points': points, 'unit_price': 0})
        return snapshot


class StockPickingPosQuotation(models.Model):
    _inherit = 'stock.picking'

    def _create_move_from_pos_order_lines(self, lines):
        # Native POS groups equal products. Separate recipes that use the same base.
        tinted = lines.filtered(lambda line: line.sale_order_line_id and line.sale_order_line_id._tint_recipe().get('base_only'))
        for sale_line in tinted.sale_order_line_id:
            super()._create_move_from_pos_order_lines(tinted.filtered(lambda line: line.sale_order_line_id == sale_line))
        if lines - tinted:
            return super()._create_move_from_pos_order_lines(lines - tinted)

    def _prepare_stock_move_vals(self, first_line, order_lines):
        values = super()._prepare_stock_move_vals(first_line, order_lines)
        sale_line = order_lines.sale_order_line_id
        if len(sale_line) == 1 and sale_line._tint_recipe().get('base_only'):
            values['pos_tint_sale_line_id'] = sale_line.id
            values['description_picking'] = sale_line.tint_color_description or ''
        return values


class SaleOrderLinePosQuotation(models.Model):
    _inherit = 'sale.order.line'

    @api.model
    def _load_pos_data_fields(self, config):
        return list(dict.fromkeys(super()._load_pos_data_fields(config) + ['tint_snapshot', 'tint_display_name']))


class StockMovePosQuotation(models.Model):
    _inherit = 'stock.move'

    pos_tint_sale_line_id = fields.Many2one('sale.order.line', readonly=True, copy=False, check_company=True,
                                           ondelete='restrict', string='Entintado de venta cobrado en caja')

    def _tint_sale_line(self):
        line = super()._tint_sale_line()
        move = self
        while not line and move:
            line = move.pos_tint_sale_line_id
            move = move.origin_returned_move_id
        return line
