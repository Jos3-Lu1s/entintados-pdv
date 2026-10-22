from collections import defaultdict

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class AccountMoveReturnCredit(models.Model):
    _inherit = 'account.move'

    stock_return_picking_id = fields.Many2one(
        'stock.picking', string='Devolución de origen', copy=False, readonly=True,
        index=True, check_company=True, ondelete='restrict')

    _stock_return_invoice_unique = models.Constraint(
        'UNIQUE(stock_return_picking_id, reversed_entry_id)',
        'Ya existe una nota de crédito para esta devolución y factura.')

    def _return_line_capacities(self):
        """Cantidades facturadas menos notas de crédito vigentes, en UdM del producto."""
        self.ensure_one()
        lines = self.invoice_line_ids.filtered(
            lambda line: line.display_type == 'product' and line.product_id and line.quantity > 0)
        capacities = {
            line.id: (line.product_uom_id or line.product_id.uom_id)._compute_quantity(
                line.quantity, line.product_id.uom_id, round=False)
            for line in lines
        }
        credits = self.env['account.move'].search([
            ('reversed_entry_id', '=', self.id), ('move_type', '=', 'out_refund'),
            ('state', 'in', ('draft', 'posted')),
        ]).invoice_line_ids.filtered(lambda line: line.display_type == 'product' and line.quantity > 0)
        for credit in credits.sorted(lambda line: (not bool(line.stock_return_source_line_id), line.id)):
            product = credit.product_id
            candidates = lines.filtered(lambda line: line.product_id == product)
            if credit.stock_return_source_line_id:
                candidates &= credit.stock_return_source_line_id
            elif credit.sale_line_ids:
                candidates = candidates.filtered(lambda line: bool(line.sale_line_ids & credit.sale_line_ids))
            remaining = (credit.product_uom_id or product.uom_id)._compute_quantity(
                credit.quantity, product.uom_id, round=False) if product else 0
            for line in candidates.sorted('id'):
                allocated = min(max(capacities[line.id], 0), remaining)
                capacities[line.id] -= allocated
                remaining -= allocated
        return capacities


class AccountMoveLineReturnCredit(models.Model):
    _inherit = 'account.move.line'

    stock_return_source_line_id = fields.Many2one(
        'account.move.line', string='Línea facturada devuelta', copy=False, readonly=True,
        index=True, check_company=True, ondelete='restrict')


class StockPickingReturnCredit(models.Model):
    _inherit = 'stock.picking'

    return_credit_note_ids = fields.One2many(
        'account.move', 'stock_return_picking_id', string='Notas de crédito de devolución', readonly=True)
    return_credit_note_count = fields.Integer(compute='_compute_return_credit_note_count')
    return_invoice_id = fields.Many2one(
        'account.move', string='Factura a devolver', copy=False, check_company=True,
        help='Selecciona la factura original si los productos devueltos aparecen en varias facturas.')
    return_invoice_candidate_ids = fields.Many2many(
        'account.move', compute='_compute_return_invoice_candidates', string='Facturas de origen disponibles')
    is_sale_customer_return = fields.Boolean(compute='_compute_is_sale_customer_return')

    @api.depends('return_credit_note_ids')
    def _compute_return_credit_note_count(self):
        for picking in self:
            picking.return_credit_note_count = len(picking.return_credit_note_ids)

    @api.depends('move_ids.origin_returned_move_id', 'move_ids.location_id.usage',
                 'move_ids.location_dest_id.usage')
    def _compute_is_sale_customer_return(self):
        for picking in self:
            picking.is_sale_customer_return = bool(picking._customer_return_moves())

    def _customer_return_moves(self):
        self.ensure_one()
        return self.move_ids.filtered(
            lambda move: move.origin_returned_move_id
            and move.location_id.usage == 'customer'
            and move.location_dest_id.usage != 'customer'
            and move.origin_returned_move_id.location_dest_id.usage == 'customer'
            and move.origin_returned_move_id.location_id.usage != 'customer')

    def _move_original_invoices(self, move):
        origin = move.origin_returned_move_id
        invoices = origin.sale_line_id.invoice_lines.move_id
        order = origin.picking_id.pos_order_id
        if order:
            invoices |= order.account_move | order.split_invoice_ids
        return invoices.filtered(
            lambda invoice: invoice.move_type == 'out_invoice' and invoice.state == 'posted'
            and invoice.company_id == self.company_id
            and invoice.l10n_mx_edi_cfdi_state != 'cancel')

    @api.depends('move_ids.origin_returned_move_id', 'company_id')
    def _compute_return_invoice_candidates(self):
        for picking in self:
            invoices = self.env['account.move']
            for move in picking._customer_return_moves():
                invoices |= picking.sudo()._move_original_invoices(move.sudo())
            picking.return_invoice_candidate_ids = invoices

    def _return_invoice_lines(self, move, invoice):
        lines = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == 'product' and line.product_id == move.product_id
            and line.quantity > 0)
        sale_line = move.origin_returned_move_id.sale_line_id
        if sale_line:
            lines = lines.filtered(lambda line: sale_line in line.sale_line_ids)
        return lines.sorted(lambda line: (line.sequence, line.id))

    def _return_credit_plan(self):
        self.ensure_one()
        plan = defaultdict(lambda: defaultdict(float))
        capacities = {}
        for move in self._customer_return_moves().filtered(lambda move: move.state == 'done' and move.quantity > 0):
            invoices = self._move_original_invoices(move)
            if self.return_invoice_id:
                if self.return_invoice_id not in invoices:
                    raise UserError(_(
                        'La factura seleccionada no corresponde a la venta original de %(product)s.',
                        product=move.product_id.display_name))
                invoices &= self.return_invoice_id
            # Solo considerar facturas que incluyan este producto/línea de venta.
            invoices = invoices.filtered(lambda invoice: bool(self._return_invoice_lines(move, invoice)))
            if not invoices:
                continue  # No hay importe facturado que acreditar para este movimiento.
            if len(invoices) > 1:
                raise UserError(_(
                    'El producto %(product)s está en varias facturas. Selecciona la «Factura a devolver» '
                    'en la devolución antes de validarla. Si abarca varias facturas, separa las devoluciones por factura.',
                    product=move.product_id.display_name))
            invoice = invoices
            if invoice.id not in capacities:
                capacities[invoice.id] = invoice._return_line_capacities()
            available = capacities[invoice.id]
            remaining = move.product_uom._compute_quantity(move.quantity, move.product_id.uom_id, round=False)
            for line in self._return_invoice_lines(move, invoice):
                quantity = min(max(available.get(line.id, 0), 0), remaining)
                if quantity <= 0:
                    continue
                plan[invoice.id][line.id] += quantity
                available[line.id] -= quantity
                remaining -= quantity
            if move.product_id.uom_id.compare(remaining, 0) > 0:
                raise UserError(_(
                    'La cantidad devuelta de %(product)s supera la cantidad facturada pendiente de acreditar '
                    'en %(invoice)s. Revisa las notas de crédito existentes, incluidas las de borrador.',
                    product=move.product_id.display_name, invoice=invoice.display_name))
        return plan

    def _create_return_credit_notes(self):
        """Solo desde una devolución validada; nunca contabiliza ni timbra la nota."""
        self.ensure_one()
        if self.state != 'done' or self.return_credit_note_ids:
            return self.return_credit_note_ids
        if ('cancelled_invoice_id' in self._fields and self.cancelled_invoice_id) or (
                self.pos_order_id and self.pos_order_id.is_refund):
            return self.env['account.move']
        notes = self.env['account.move']
        for invoice_id, quantities in self._return_credit_plan().items():
            invoice = self.env['account.move'].browse(invoice_id)
            line_commands = []
            for line in invoice.invoice_line_ids.filtered(lambda line: line.id in quantities):
                quantity = line.product_id.uom_id._compute_quantity(
                    quantities[line.id], line.product_uom_id or line.product_id.uom_id, round=False)
                line_commands.append(Command.create({
                    'display_type': 'product', 'sequence': line.sequence,
                    'product_id': line.product_id.id, 'name': line.name,
                    'account_id': line.account_id.id, 'product_uom_id': line.product_uom_id.id,
                    'quantity': quantity, 'price_unit': line.price_unit, 'discount': line.discount,
                    'tax_ids': [Command.set(line.tax_ids.ids)],
                    'analytic_distribution': line.analytic_distribution,
                    'sale_line_ids': [Command.set(line.sale_line_ids.ids)],
                    'stock_return_source_line_id': line.id,
                }))
            today = fields.Date.context_today(self)
            note = invoice._reverse_moves(default_values_list=[{
                'date': today, 'invoice_date': today, 'auto_post': 'no',
                'ref': _('Devolución %(picking)s de %(invoice)s', picking=self.name, invoice=invoice.name),
                'stock_return_picking_id': self.id, 'line_ids': line_commands,
            }], cancel=False)
            notes |= note
            note.message_post(body=Markup('%s %s') % (
                _('Nota de crédito en borrador por la devolución'), self._get_html_link()))
            self.message_post(body=Markup('%s %s %s %s') % (
                _('Nota de crédito en borrador:'), note._get_html_link(),
                _('Factura original:'), invoice._get_html_link()))
        return notes

    @api.model
    def _create_picking_from_pos_order_lines(self, *args, **kwargs):
        # El POS valida sus movimientos antes de enlazar pos_order_id y después
        # genera su propia factura de reembolso. No duplicarla en este punto.
        return super(StockPickingReturnCredit, self.with_context(
            skip_stock_return_credit=True))._create_picking_from_pos_order_lines(*args, **kwargs)

    def _action_done(self):
        result = super()._action_done()
        if self.env.context.get('skip_stock_return_credit'):
            return result
        returns = self.filtered(lambda picking: picking.state == 'done' and picking._customer_return_moves())
        if returns:
            # La operación automática crea únicamente borradores, sin ampliar permisos del almacén.
            returns = returns.sudo()
            self.env['res.company']._with_locked_records(returns.sorted('id'))
            returns.invalidate_recordset(['return_credit_note_ids'])
            invoices = self.env['account.move'].sudo()
            for picking in returns:
                for move in picking._customer_return_moves():
                    invoices |= picking._move_original_invoices(move)
            if invoices:
                self.env['res.company']._with_locked_records(invoices.sorted('id'))
            for picking in returns:
                picking._create_return_credit_notes()
        return result

    def action_view_return_credit_notes(self):
        self.ensure_one()
        self.check_access('read')
        notes = self.return_credit_note_ids
        action = {
            'type': 'ir.actions.act_window', 'name': _('Notas de crédito de devolución'),
            'res_model': 'account.move', 'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')], 'domain': [('id', 'in', notes.ids)],
        }
        if len(notes) == 1:
            action.update(view_mode='form', views=[(False, 'form')], res_id=notes.id)
        return action
