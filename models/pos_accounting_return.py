"""Devoluciones de tickets cerrados sin factura, autorizadas desde Contabilidad."""
from collections import defaultdict

from markupsafe import Markup
from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from .business_chatter import create_audit, operation, unlink_audit, write_audit, PARENTS

PARENTS['pos.accounting.return.line'] = 'return_id'

ACTIVE_STATES = ('waiting_stock', 'returned', 'done')


class PosAccountingReturn(models.Model):
    _name = 'pos.accounting.return'
    _description = 'Devolución contable de ticket sin factura'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _check_company_auto = True
    _order = 'id desc'

    name = fields.Char(string='Folio', required=True, default=lambda self: _('New'), readonly=True, copy=False)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    order_id = fields.Many2one('pos.order', string='Ticket pagado sin factura', required=True, check_company=True,
                              domain="[('accounting_return_eligible', '=', True), ('company_id', '=', company_id)]", tracking=True)
    source_move_id = fields.Many2one(related='order_id.session_id.move_id', string='Asiento original', readonly=True)
    currency_id = fields.Many2one(related='order_id.currency_id')
    partner_id = fields.Many2one('res.partner', string='Cliente', required=True, check_company=True, tracking=True)
    date = fields.Date(string='Fecha', default=fields.Date.context_today, required=True, tracking=True)
    reason = fields.Char(string='Motivo', required=True, tracking=True)
    return_lot_names = fields.Text(
        string='Lotes o series devueltos', tracking=True,
        help='Opcional si el lote es único. En devoluciones parciales con varios lotes o series, '
             'captura los identificadores que regresan, uno por renglón. Para cantidades '
             'parciales de distintos lotes, registra una devolución por lote.')
    journal_id = fields.Many2one('account.journal', string='Diario de notas de crédito', required=True,
                                check_company=True, tracking=True, domain="[('type', '=', 'sale'), ('company_id', '=', company_id)]")
    state = fields.Selection([('draft', 'Borrador'), ('waiting_stock', 'Pendiente de recepción automática'),
                              ('returned', 'Inventario actualizado / pendiente de nota'), ('done', 'Completado'), ('cancel', 'Cancelado')],
                             default='draft', readonly=True, copy=False, required=True, tracking=True)
    line_ids = fields.One2many('pos.accounting.return.line', 'return_id', string='Productos', copy=False)
    picking_ids = fields.One2many('stock.picking', 'pos_accounting_return_id', readonly=True, string='Recepciones')
    credit_ids = fields.One2many('account.move', 'pos_accounting_return_id', readonly=True, string='Notas de crédito')
    payment_ids = fields.One2many('account.payment', 'pos_accounting_return_id', readonly=True, string='Devoluciones de dinero')
    amount_total = fields.Monetary(compute='_compute_totals', string='Importe a devolver')
    stock_received = fields.Boolean(compute='_compute_totals')
    receipt_names = fields.Char(compute='_compute_totals', string='Recepciones de almacén')

    @api.depends('line_ids.quantity', 'line_ids.selected', 'line_ids.amount', 'picking_ids.state')
    def _compute_totals(self):
        for record in self:
            record.amount_total = sum(record.line_ids.filtered('selected').mapped('amount'))
            pickings = record.sudo().picking_ids
            record.stock_received = bool(pickings) and all(p.state == 'done' for p in pickings)
            record.receipt_names = ', '.join(pickings.mapped('name'))

    @api.onchange('order_id')
    def _onchange_order(self):
        self.line_ids = [Command.clear()]
        if self.order_id:
            self.partner_id = self.order_id.partner_id
            self.company_id = self.order_id.company_id
            self.journal_id = self.order_id.config_id.invoice_journal_id or self.env['account.journal'].search([
                ('company_id', '=', self.company_id.id), ('type', '=', 'sale')], limit=1)
            self.line_ids = [Command.create({'source_line_id': line.id}) for line in self.order_id.lines
                             if not line.combo_parent_id and line.qty > line.refunded_qty
                             and line.product_id.type != 'service' and not line._is_accounting_advance_line()]

    def _check_editable(self):
        if any(r.state != 'draft' for r in self):
            raise UserError(_('Solo puedes modificar una devolución en borrador.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('state', 'draft') != 'draft':
                raise UserError(_('Las devoluciones deben crearse en borrador.'))
            vals['name'] = self.env['ir.sequence'].next_by_code(self._name) or _('New')
        return super().create(vals_list)

    def write(self, vals):
        if 'state' in vals:
            raise UserError(_('Usa los botones de la devolución para cambiar su etapa.'))
        if 'return_lot_names' in vals and any(r.state not in ('draft', 'waiting_stock') for r in self):
            raise UserError(_('Los lotes o series solo pueden capturarse antes de recibir la devolución.'))
        if set(vals) & {'order_id', 'partner_id', 'company_id', 'date', 'reason', 'journal_id', 'line_ids'}:
            self._check_editable()
        return super().write(vals)

    def unlink(self):
        self._check_editable()
        return super().unlink()

    def _expanded_lines(self):
        """Un entintado/combo devuelve todos sus componentes proporcionalmente."""
        result = []
        for row in self.line_ids.filtered('selected'):
            root = row.source_line_id
            family, pending = root, root.combo_line_ids
            while pending:
                new = pending - family
                family |= new
                pending = new.combo_line_ids - family
            for source in family:
                result.append((source, source.qty * row.quantity / root.qty))
        return result

    def _validate_source(self, quantities=True):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_invoice'):
            raise UserError(_('Esta devolución requiere permisos de Contabilidad.'))
        if self.company_id not in self.env.companies or self.order_id.company_id != self.company_id:
            raise UserError(_('La compañía del ticket no está habilitada.'))
        self.order_id.check_access('read')
        if not self.order_id._can_accounting_return():
            raise UserError(_('El ticket debe estar pagado, sin factura individual/dividida/global y con su sesión cerrada y contabilizada.'))
        if self.journal_id.type != 'sale' or self.journal_id.company_id != self.company_id:
            raise UserError(_('Selecciona un diario de ventas de la compañía del ticket.'))
        if self.journal_id.currency_id and self.journal_id.currency_id != self.currency_id:
            raise UserError(_('La moneda del diario no coincide con el ticket.'))
        selected = self.line_ids.filtered('selected')
        if not selected or len(selected.source_line_id) != len(selected):
            raise UserError(_('Selecciona productos sin repetir líneas del ticket.'))
        for row in selected:
            line = row.source_line_id
            if (line.order_id != self.order_id or line.combo_parent_id or line.qty <= 0
                    or line.product_id.type == 'service' or line._is_accounting_advance_line()):
                raise UserError(_('Selecciona únicamente productos del ticket; los componentes se incluyen automáticamente.'))
            remaining = line.qty - line.refunded_qty
            if row.quantity <= 0 or (quantities and float_compare(row.quantity, remaining,
                    precision_rounding=line.product_uom_id.rounding) > 0):
                raise UserError(_('La cantidad de %s debe ser positiva y no superar lo pendiente de devolver.') % line.product_id.display_name)
        if self.currency_id.compare_amounts(self.amount_total, 0) <= 0:
            raise UserError(_('El importe a devolver debe ser positivo.'))
        expanded = self._expanded_lines()
        names = self._selected_return_lot_names()
        if names:
            delivered = self.order_id.sudo().picking_ids.move_ids.filtered(
                lambda m: m.state == 'done' and m.location_dest_id.usage == 'customer'
                and m.product_id.id in {line.product_id.id for line, qty in expanded})
            known = set(delivered.move_line_ids.lot_id.mapped('name'))
            if names - known:
                raise UserError(_('Estos lotes o series no pertenecen a los productos entregados en el ticket: %s')
                                % ', '.join(sorted(names - known)))
        return expanded

    def _selected_return_lot_names(self):
        self.ensure_one()
        return {name.strip() for name in (self.return_lot_names or '').splitlines() if name.strip()}

    def _payment_distribution(self):
        weights = defaultdict(float)
        for payment in self.order_id.payment_ids:
            if payment.payment_method_id.type != 'pay_later':
                weights[payment.payment_method_id.journal_id.id] += payment.amount
        result = []
        for journal_id, weight in sorted(weights.items()):
            if weight <= 0:
                continue
            journal = self.env['account.journal'].browse(journal_id)
            method = journal.outbound_payment_method_line_ids.filtered(lambda m: m.code == 'manual')[:1]
            if journal.type not in ('bank', 'cash') or not method or not (method.payment_account_id or journal.default_account_id):
                raise UserError(_('Revisa el diario y la cuenta del pago original del ticket: %s.') % journal.display_name)
            result.append((journal, method, weight))
        if not result:
            raise UserError(_('No se encontró un pago de banco/caja del ticket.'))
        return result

    def action_prepare_return(self):
        self.ensure_one()
        self.env['res.company']._with_locked_records(self)
        self.invalidate_recordset()
        if self.state == 'waiting_stock':
            self.env['res.company']._with_locked_records(self.order_id)
            self._validate_source(quantities=False)
            self._payment_distribution()
            self._auto_validate_return_pickings()
            return True
        if self.state != 'draft':
            return True
        self.env['res.company']._with_locked_records(self.order_id)
        self.order_id.invalidate_recordset()
        expanded = self._validate_source()
        self._payment_distribution()
        requested = defaultdict(float)
        for line, quantity in expanded:
            if line.product_id.type == 'consu':
                requested[line.product_id.id] += line.product_uom_id._compute_quantity(quantity, line.product_id.uom_id, round=False)
        pickings = self.order_id.sudo().picking_ids.filtered(lambda p: p.state == 'done' and p.location_dest_id.usage == 'customer')
        moves = pickings.move_ids.filtered(lambda m: m.state == 'done')
        self.env['res.company']._with_locked_records(moves.sorted('id'))
        with operation(self, 'Preparar devolución de ticket sin factura'):
            for picking in pickings.sorted('id'):
                wizard = self.env['stock.return.picking'].sudo().with_context(
                    skip_stock_return_credit=True).create({'picking_id': picking.id})
                any_quantity = False
                for row in wizard.product_return_moves:
                    product = row.product_id
                    # Restar también devoluciones anteriores aún en preparación.
                    original = row.move_id
                    taken = sum(m.product_uom._compute_quantity(m.product_uom_qty, original.product_uom, round=False)
                                for m in original.returned_move_ids.filtered(lambda m: m.state != 'cancel'))
                    capacity = max(original.quantity - taken, 0)
                    capacity = original.product_uom._compute_quantity(capacity, product.uom_id, round=False)
                    selected_names = self._selected_return_lot_names()
                    if selected_names and product.tracking != 'none':
                        product_lots = moves.filtered(lambda m: m.product_id == product).move_line_ids.lot_id
                        product_names = selected_names & set(product_lots.mapped('name'))
                        if product_names:
                            selected_lines = original.move_line_ids.filtered(lambda l: l.lot_id.name in product_names)
                            selected_quantity = sum(l.product_uom_id._compute_quantity(
                                l.quantity, product.uom_id, round=False) for l in selected_lines)
                            used_lines = original.returned_move_ids.filtered(
                                lambda m: m.state != 'cancel').move_line_ids.filtered(lambda l: l.lot_id.name in product_names)
                            selected_quantity -= sum(l.product_uom_id._compute_quantity(
                                l.quantity, product.uom_id, round=False) for l in used_lines)
                            capacity = min(capacity, max(selected_quantity, 0))
                    qty = min(requested[product.id], capacity)
                    row.quantity = product.uom_id._compute_quantity(qty, row.uom_id, round=False)
                    requested[product.id] -= qty
                    any_quantity |= qty > 0
                if any_quantity:
                    returned = wizard._create_return()
                    returned.write({'pos_accounting_return_id': self.id, 'origin': self.name,
                                    'partner_id': self.partner_id.id, 'pos_order_id': False, 'pos_session_id': False})
            if any(not self.env['product.product'].browse(pid).uom_id.is_zero(qty) for pid, qty in requested.items()):
                raise UserError(_('No hay una entrega realizada con cantidad suficiente para todos los productos seleccionados.'))
            if not self.sudo().picking_ids:
                raise UserError(_('No hay productos físicos entregados para devolver.'))
            super(PosAccountingReturn, self).write({'state': 'waiting_stock'})
            self._auto_validate_return_pickings()
        return True

    def _auto_validate_return_pickings(self):
        """Only this authorized ticket return receives stock automatically."""
        self.ensure_one()
        pickings = self.sudo().picking_ids
        if not pickings or pickings.filtered(lambda p: p.state == 'cancel'):
            raise UserError(_('No se puede completar la devolución: falta una recepción válida de inventario.'))
        pending = pickings.filtered(lambda p: p.state != 'done').sorted('id')
        self.env['res.company']._with_locked_records(pending)
        for picking in pending:
            for move in picking.move_ids.filtered(lambda m: m.state not in ('done', 'cancel')):
                original = move.origin_returned_move_id
                if (not original or original.picking_id.pos_order_id != self.order_id
                        or move.location_id.usage != 'customer' or move.location_dest_id.usage != 'internal'):
                    raise UserError(_('La recepción no corresponde a una entrega original del ticket.'))
                if move.product_id.tracking != 'none':
                    self._prepare_return_tracking(move)
                else:
                    move.quantity = move.product_uom_qty
                move.picked = True
            picking.with_context(skip_stock_return_credit=True, skip_sms=True,
                                 skip_backorder=True).button_validate()
        if any(p.state != 'done' for p in pickings):
            raise UserError(_('No se pudo validar automáticamente toda la devolución de inventario.'))
        self._check_received_quantities()
        if self.state == 'waiting_stock':
            super(PosAccountingReturn, self).write({'state': 'returned'})

    def _prepare_return_tracking(self, move):
        """Reuse an unambiguous original lot/serial, never invent traceability."""
        original = move.origin_returned_move_id
        capacities = defaultdict(float)
        for line in original.move_line_ids.filtered(lambda l: l.state == 'done' and l.quantity > 0):
            if not line.lot_id:
                raise UserError(_('La entrega original de %s no tiene lote o serie.') % move.product_id.display_name)
            capacities[(line.lot_id.id, line.owner_id.id)] += line.product_uom_id._compute_quantity(
                line.quantity, move.product_id.uom_id, round=False)
        previous = original.returned_move_ids.filtered(lambda m: m.state == 'done' and m != move)
        for line in previous.move_line_ids:
            capacities[(line.lot_id.id, line.owner_id.id)] -= line.product_uom_id._compute_quantity(
                line.quantity, move.product_id.uom_id, round=False)
        available = {key: qty for key, qty in capacities.items() if move.product_id.uom_id.compare(qty, 0) > 0}
        selected_names = self._selected_return_lot_names()
        matching = {key: qty for key, qty in available.items()
                    if self.env['stock.lot'].sudo().browse(key[0]).name in selected_names}
        original_names = {self.env['stock.lot'].sudo().browse(key[0]).name for key in capacities}
        if selected_names & original_names:
            available = matching
        quantity = move.product_uom._compute_quantity(move.product_uom_qty, move.product_id.uom_id, round=False)
        if (not available or move.product_id.uom_id.compare(quantity, sum(available.values())) > 0
                or (len(available) > 1 and move.product_id.uom_id.compare(quantity, sum(available.values())) != 0)):
            raise UserError(_(
                'No se puede identificar automáticamente el lote o serie de %(product)s para una devolución parcial. '
                'Captura los identificadores correspondientes en «Lotes o series devueltos» y vuelve a registrar la devolución.',
                product=move.product_id.display_name))
        # Discard customer-location reservations that may belong to a different sale.
        move.move_line_ids.unlink()
        for (lot_id, owner_id), available_quantity in available.items():
            allocated = min(quantity, available_quantity)
            self.env['stock.move.line'].sudo().create({
                'move_id': move.id, 'picking_id': move.picking_id.id, 'company_id': move.company_id.id,
                'product_id': move.product_id.id, 'product_uom_id': move.product_uom.id,
                'quantity': move.product_id.uom_id._compute_quantity(allocated, move.product_uom, round=False),
                'lot_id': lot_id, 'owner_id': owner_id,
                'location_id': move.location_id.id, 'location_dest_id': move.location_dest_id.id,
            })
            quantity -= allocated

    def _check_received_quantities(self):
        pickings = self.sudo().picking_ids
        if not pickings or any(p.state != 'done' for p in pickings):
            raise UserError(_('Registra primero la devolución para actualizar automáticamente el inventario.'))
        expected, actual = defaultdict(float), defaultdict(float)
        for line, qty in self._expanded_lines():
            if line.product_id.type == 'consu':
                expected[line.product_id.id] += line.product_uom_id._compute_quantity(qty, line.product_id.uom_id, round=False)
        for move in pickings.move_ids.filtered(lambda m: m.state == 'done'):
            if (move.location_id.usage != 'customer' or move.location_dest_id.usage != 'internal'
                    or move.origin_returned_move_id.picking_id.pos_order_id != self.order_id):
                raise UserError(_('La recepción debe provenir de la entrega original del ticket y regresar al almacén.'))
            actual[move.product_id.id] += move.product_uom._compute_quantity(move.quantity, move.product_id.uom_id, round=False)
        for pid in set(expected) | set(actual):
            product = self.env['product.product'].browse(pid)
            if float_compare(expected[pid], actual[pid], precision_rounding=product.uom_id.rounding):
                raise UserError(_('La recepción de %s no coincide con lo solicitado. Revisa la devolución antes de generar la nota.') % product.display_name)

    def _credit_line_values(self, source, qty):
        base = source._prepare_base_line_for_taxes_computation()
        taxes = source.tax_ids_after_fiscal_position
        candidates = self.source_move_id.line_ids.filtered(lambda l: not l.tax_line_id and l.credit > 0
            and (l.account_id.account_type in ('income', 'income_other') or l.account_id == base['account_id'])
            and set(l.tax_ids.ids) == set(taxes.flatten_taxes_hierarchy().ids) and (not l.product_id or l.product_id == source.product_id))
        accounts = candidates.account_id
        account = base['account_id'] if base['account_id'] in accounts else accounts if len(accounts) == 1 else False
        if not account:
            raise UserError(_('No se pudo identificar de forma inequívoca la cuenta de ingreso original de %s en el asiento del cierre.') % source.product_id.display_name)
        product = self.env['account.move']._reajuste_product()
        return {'product_id': product.id, 'name': 'Reajuste',
                'pos_return_source_line_id': source.id, 'return_source_quantity': qty,
                'quantity': 1, 'product_uom_id': product.uom_id.id, 'price_unit': source.price_unit * qty,
                'discount': source.discount, 'tax_ids': [Command.set(taxes.ids)], 'account_id': account.id}

    def action_create_credit(self):
        self.ensure_one()
        self.env['res.company']._with_locked_records(self)
        self.invalidate_recordset()
        if self.state == 'done':
            return self.action_open_credits()
        if self.state not in ('waiting_stock', 'returned'):
            raise UserError(_('Prepara y recibe la devolución antes de generar la nota.'))
        self.env['res.company']._with_locked_records(self.order_id)
        expanded = self._validate_source(quantities=False)
        self._check_received_quantities()
        if self.credit_ids.filtered(lambda c: c.state != 'cancel'):
            raise UserError(_('Ya existe una nota activa para esta devolución.'))
        distribution = self._payment_distribution()
        with operation(self, 'Nota y devolución de importe de ticket sin factura'):
            credit = self.env['account.move'].create({
                'move_type': 'out_refund', 'partner_id': self.partner_id.id, 'journal_id': self.journal_id.id,
                'company_id': self.company_id.id, 'currency_id': self.currency_id.id,
                'invoice_date': self.date, 'date': self.date, 'ref': '%s: %s' % (self.name, self.reason),
                'invoice_origin': self.order_id.pos_reference or self.order_id.name,
                'pos_accounting_return_id': self.id, 'pos_return_source_move_id': self.source_move_id.id,
                'invoice_line_ids': [Command.create(self._credit_line_values(line, qty)) for line, qty in expanded
                                     if line.product_id.type != 'combo'],
            })
            if self.currency_id.compare_amounts(credit.amount_total, self.amount_total):
                raise UserError(_('Los impuestos o redondeos actuales no coinciden con el importe del ticket. Revisa su configuración.'))
            previous = self.env['pos.accounting.return'].search([
                ('order_id', '=', self.order_id.id), ('state', '=', 'done'), ('id', '!=', self.id)])
            if self.currency_id.compare_amounts(sum(previous.mapped('amount_total')) + credit.amount_total, self.order_id.amount_total) > 0:
                raise UserError(_('Las devoluciones no pueden superar el importe pagado del ticket.'))
            credit.action_post()
            total, cumulative, allocated = sum(x[2] for x in distribution), 0, 0
            for journal, method, weight in distribution:
                cumulative += weight
                target = self.currency_id.round(credit.amount_total * cumulative / total)
                share, allocated = self.currency_id.round(target - allocated), target
                if self.currency_id.is_zero(share):
                    continue
                wizard = self.env['account.payment.register'].with_context(
                    active_model='account.move', active_ids=credit.ids,
                    customer_adjustment_refund_account_id=(method.payment_account_id or journal.default_account_id).id,
                ).create({'journal_id': journal.id, 'payment_method_line_id': method.id,
                          'payment_date': self.date, 'currency_id': self.currency_id.id,
                          'amount': share, 'custom_user_amount': share, 'custom_user_currency_id': self.currency_id.id,
                          'payment_difference_handling': 'open', 'group_payment': True})
                payments = wizard._create_payments()
                if not payments or any(p.move_id.state != 'posted' for p in payments):
                    raise UserError(_('No fue posible contabilizar la devolución del dinero.'))
                payments.write({'pos_accounting_return_id': self.id, 'pos_accounting_return_credit_id': credit.id})
            if not self.currency_id.is_zero(credit.amount_residual):
                raise UserError(_('No fue posible conciliar completamente la nota de crédito.'))
            super(PosAccountingReturn, self).write({'state': 'done'})
            self.source_move_id.message_post(body=Markup('Devolución de ticket sin factura: %s · Nota: %s') %
                                            (self._get_html_link(), credit._get_html_link()))
        return self.action_open_credits()

    def action_cancel(self):
        for record in self:
            record.check_access('write')
            self.env['res.company']._with_locked_records(record)
            if record.state == 'cancel':
                continue
            if record.credit_ids.filtered(lambda c: c.state != 'cancel') or record.sudo().picking_ids.filtered(lambda p: p.state == 'done'):
                raise UserError(_('No puedes cancelar una devolución ya recibida. Puedes corregir y regenerar su nota conservando la recepción.'))
            record.sudo().picking_ids.filtered(lambda p: p.state != 'cancel').action_cancel()
            super(PosAccountingReturn, record).write({'state': 'cancel'})
        return True

    def action_open_credits(self):
        self.ensure_one()
        return self.env['account.move']._action_open_moves(self.credit_ids, _('Notas de devolución'))

    def action_open_pickings(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Recepciones de devolución'), 'res_model': 'stock.picking',
                'view_mode': 'list,form', 'domain': [('pos_accounting_return_id', '=', self.id)]}


class PosAccountingReturnLine(models.Model):
    _name = 'pos.accounting.return.line'
    _description = 'Producto de devolución contable del POS'
    _check_company_auto = True

    return_id = fields.Many2one('pos.accounting.return', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='return_id.company_id', store=True)
    currency_id = fields.Many2one(related='return_id.currency_id')
    source_line_id = fields.Many2one('pos.order.line', string='Línea del ticket', required=True, check_company=True)
    product_id = fields.Many2one(related='source_line_id.product_id')
    sold_qty = fields.Float(related='source_line_id.qty', string='Vendido')
    refunded_qty = fields.Float(related='source_line_id.refunded_qty', string='Devuelto / reservado')
    selected = fields.Boolean(string='Seleccionar')
    quantity = fields.Float(string='Cantidad a devolver', digits='Product Unit')
    amount = fields.Monetary(compute='_compute_amount', string='Importe con impuestos')

    @api.depends('source_line_id', 'quantity')
    def _compute_amount(self):
        for row in self:
            root = row.source_line_id
            family, pending = root, root.combo_line_ids
            while pending:
                new = pending - family
                family |= new
                pending = new.combo_line_ids - family
            row.amount = sum(family.mapped('price_subtotal_incl')) * row.quantity / root.qty if root.qty else 0

    @api.onchange('selected')
    def _onchange_selected(self):
        if self.selected and not self.quantity:
            self.quantity = max(self.sold_qty - self.refunded_qty, 0)

    @api.model_create_multi
    def create(self, vals_list):
        self.env['pos.accounting.return'].browse([v['return_id'] for v in vals_list if v.get('return_id')])._check_editable()
        return create_audit(self, vals_list, super().create)

    def write(self, vals):
        self.return_id._check_editable()
        if vals.get('return_id'):
            self.env['pos.accounting.return'].browse(vals['return_id'])._check_editable()
        return write_audit(self, vals, super().write)

    def unlink(self):
        self.return_id._check_editable()
        return unlink_audit(self, super().unlink)


class PosOrderAccountingReturn(models.Model):
    _inherit = 'pos.order'

    accounting_return_ids = fields.One2many('pos.accounting.return', 'order_id')
    accounting_return_eligible = fields.Boolean(compute='_compute_accounting_return_eligible', store=True, index=True)
    accounting_source_move_id = fields.Many2one(related='session_id.move_id', string='Asiento del cierre')

    def _can_accounting_return(self):
        self.ensure_one()
        collected = sum(self.payment_ids.filtered(lambda p: p.payment_method_id.type != 'pay_later').mapped('amount'))
        rounding = self.config_id.rounding_method.rounding if self.config_id.cash_rounding else self.currency_id.rounding
        return (self.state in ('paid', 'done') and self.amount_total > 0 and not self.is_refund
                and not self.account_move and not self.split_invoice_ids
                and self.l10n_mx_edi_cfdi_state not in ('sent', 'global_sent')
                and self.session_id.state == 'closed' and self.session_id.move_id.state == 'posted'
                and float_compare(collected, self.amount_total, precision_rounding=rounding) >= 0)

    @api.depends('state', 'amount_total', 'is_refund', 'account_move', 'split_invoice_ids', 'l10n_mx_edi_cfdi_state',
                 'session_id.state', 'session_id.move_id.state', 'payment_ids.amount', 'payment_ids.payment_method_id.type',
                 'lines.qty', 'lines.combo_parent_id', 'lines.product_id.type',
                 'lines.refund_orderline_ids.qty', 'lines.refund_orderline_ids.order_id.state',
                 'lines.accounting_return_line_ids.return_id.state', 'lines.accounting_return_line_ids.selected',
                 'lines.accounting_return_line_ids.quantity')
    def _compute_accounting_return_eligible(self):
        for order in self:
            order.accounting_return_eligible = order._can_accounting_return() and any(
                not line.combo_parent_id and line.product_id.type != 'service' and not line._is_accounting_advance_line()
                and float_compare(line.qty - line.refunded_qty, 0, precision_rounding=line.product_uom_id.rounding) > 0
                for line in order.lines)

    def action_new_accounting_return(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('account.group_account_invoice') or not self._can_accounting_return():
            raise UserError(_('Solo Contabilidad puede devolver tickets pagados, cerrados y sin factura.'))
        return {'type': 'ir.actions.act_window', 'name': _('Devolución de ticket sin factura'),
                'res_model': 'pos.accounting.return', 'view_mode': 'form',
                'context': {'default_order_id': self.id, 'default_partner_id': self.partner_id.id,
                            'default_company_id': self.company_id.id,
                            'default_journal_id': self.config_id.invoice_journal_id.id}}

    def _check_accounting_return_invoicing(self):
        if self.sudo().accounting_return_ids.filtered(lambda r: r.state in ACTIVE_STATES):
            raise UserError(_('Este ticket tiene una devolución de Contabilidad. No puedes facturarlo nuevamente por el importe original.'))

    def _generate_pos_order_invoice(self):
        self._check_accounting_return_invoicing()
        return super()._generate_pos_order_invoice()

    def _l10n_mx_edi_check_orders_for_global_invoice(self, origin=None):
        self._check_accounting_return_invoicing()
        return super()._l10n_mx_edi_check_orders_for_global_invoice(origin=origin)


class PosOrderLineAccountingReturn(models.Model):
    _inherit = 'pos.order.line'

    accounting_return_line_ids = fields.One2many('pos.accounting.return.line', 'source_line_id')

    @api.depends('refund_orderline_ids', 'refund_orderline_ids.order_id.state',
                 'accounting_return_line_ids.return_id.state', 'accounting_return_line_ids.quantity',
                 'accounting_return_line_ids.selected', 'combo_parent_id.accounting_return_line_ids.return_id.state',
                 'combo_parent_id.accounting_return_line_ids.quantity', 'combo_parent_id.accounting_return_line_ids.selected')
    def _compute_refund_qty(self):
        super()._compute_refund_qty()
        for line in self:
            root = line.sudo()
            seen = set()
            while root.combo_parent_id and root.id not in seen:
                seen.add(root.id)
                root = root.combo_parent_id
            rows = root.accounting_return_line_ids.filtered(lambda r: r.selected and r.return_id.state in ACTIVE_STATES)
            line.refunded_qty += sum(rows.mapped('quantity')) * line.qty / root.qty if root.qty else 0


class StockPickingAccountingReturn(models.Model):
    _inherit = 'stock.picking'

    pos_accounting_return_id = fields.Many2one('pos.accounting.return', readonly=True, copy=False,
        check_company=True, index=True, groups='account.group_account_invoice')
    pos_accounting_return_name = fields.Char(related='pos_accounting_return_id.name', string='Devolución de Contabilidad')

    def _create_backorder(self, *args, **kwargs):
        backorders = super()._create_backorder(*args, **kwargs)
        for picking in backorders:
            request = picking.sudo().backorder_id.pos_accounting_return_id
            if request:
                picking.sudo().pos_accounting_return_id = request
        return backorders

    def _action_done(self):
        result = super()._action_done()
        for record in self.sudo().pos_accounting_return_id:
            if record.state == 'waiting_stock' and record.picking_ids and all(p.state == 'done' for p in record.picking_ids):
                super(PosAccountingReturn, record).write({'state': 'returned'})
        return result


class AccountMovePosReturn(models.Model):
    _inherit = 'account.move'

    pos_accounting_return_id = fields.Many2one('pos.accounting.return', readonly=True, copy=False,
        check_company=True, index=True, groups='account.group_account_invoice')
    pos_return_source_move_id = fields.Many2one('account.move', string='Asiento de venta original', readonly=True, copy=False, check_company=True)

    def button_cancel(self):
        credits = self.filtered(lambda c: c.pos_accounting_return_id and c.move_type == 'out_refund')
        payments = self.env['account.payment'].search([('pos_accounting_return_credit_id', 'in', credits.ids), ('state', '!=', 'canceled')]) if credits else self.env['account.payment']
        if credits:
            payments |= credits._get_reconciled_payments().filtered(lambda p: p.payment_type == 'outbound' and p.state != 'canceled')
            self.env['res.company']._with_locked_records(credits.sorted('id'))
            self.env['res.company']._with_locked_records(payments.sorted('id'))
        payments._check_adjustment_cancellation(credits, self.env['customer.account.adjustment'])
        for credit in credits:
            related = payments & credit._get_reconciled_payments()
            related.write({'pos_accounting_return_id': credit.pos_accounting_return_id.id,
                           'pos_accounting_return_credit_id': credit.id})
        result = super().button_cancel()
        if payments:
            payments.action_cancel()
        for record in credits.pos_accounting_return_id:
            if not record.credit_ids.filtered(lambda c: c.state != 'cancel'):
                super(PosAccountingReturn, record).write({'state': 'returned'})
        return result


class AccountPaymentPosReturn(models.Model):
    _inherit = 'account.payment'

    pos_accounting_return_id = fields.Many2one('pos.accounting.return', readonly=True, copy=False,
        check_company=True, index=True, groups='account.group_account_invoice')
    pos_accounting_return_credit_id = fields.Many2one('account.move', readonly=True, copy=False, check_company=True, index=True)
