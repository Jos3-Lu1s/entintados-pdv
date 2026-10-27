from odoo import _, api, fields, models


class AccountMoveReturnNavigation(models.Model):
    _inherit = 'account.move'

    return_related_invoice_ids = fields.Many2many('account.move', compute='_compute_return_navigation')
    return_related_picking_ids = fields.Many2many('stock.picking', compute='_compute_return_navigation')
    return_related_payment_ids = fields.Many2many('account.payment', compute='_compute_return_navigation')
    return_related_credit_ids = fields.Many2many('account.move', compute='_compute_return_navigation')
    return_invoice_count = fields.Integer(compute='_compute_return_navigation')
    return_picking_count = fields.Integer(compute='_compute_return_navigation')
    return_payment_count = fields.Integer(compute='_compute_return_navigation')
    return_credit_count = fields.Integer(compute='_compute_return_navigation')
    has_return_credit = fields.Boolean(compute='_compute_return_navigation', search='_search_has_return_credit')

    @api.model
    def _reajuste_product(self):
        return self.env.ref('entintados_pdv.product_return_adjustment')

    @api.depends('move_type', 'reversed_entry_id', 'reversal_move_ids.state',
                 'stock_return_picking_id', 'customer_adjustment_id.payment_ids.state',
                 'pos_accounting_return_id.picking_ids.state', 'pos_accounting_return_id.payment_ids.state')
    def _compute_return_navigation(self):
        for move in self:
            source = move if move.move_type == 'out_invoice' else move.reversed_entry_id
            invoices = self.env['account.move']
            if source and source.move_type == 'out_invoice':
                invoices = self.env['customer.account.adjustment']._split_invoices(source)
            move.return_related_invoice_ids = invoices
            move.return_invoice_count = len(invoices) if move.move_type == 'out_refund' or len(invoices) > 1 else 0
            request = move.pos_accounting_return_id
            pickings = move.stock_return_picking_id | request.picking_ids
            move.return_related_picking_ids = pickings
            move.return_picking_count = len(pickings)
            payments = self.env['account.payment'].search([
                '|', ('customer_adjustment_credit_id', '=', move.id),
                ('pos_accounting_return_credit_id', '=', move.id),
            ]) if move.id and move.move_type == 'out_refund' else self.env['account.payment']
            move.return_related_payment_ids = payments
            move.return_payment_count = len(payments)
            credits = self.env['account.move'].search([
                ('reversed_entry_id', '=', move.id), ('move_type', '=', 'out_refund'),
                ('state', 'in', ['draft', 'posted']),
            ]) if move.id and move.move_type == 'out_invoice' else self.env['account.move']
            move.return_related_credit_ids = credits
            move.return_credit_count = len(credits)
            move.has_return_credit = bool(credits)

    @api.model
    def _search_has_return_credit(self, operator, value):
        if operator in ('in', 'not in'):
            values = set(value)
            if not values or values == {True, False}:
                matches_all = bool(values) == (operator == 'in')
                return [] if matches_all else [('id', '=', 0)]
            positive = (True in values) == (operator == 'in')
        elif operator in ('=', '!='):
            positive = bool(value) == (operator == '=')
        else:
            return NotImplemented
        credit_domain = [('move_type', '=', 'out_refund'), ('state', 'in', ['draft', 'posted'])]
        if positive:
            return [('move_type', '=', 'out_invoice'), ('reversal_move_ids', 'any', credit_domain)]
        return ['|', ('move_type', '!=', 'out_invoice'), ('reversal_move_ids', 'not any', credit_domain)]

    def _return_open_records(self, records, title):
        self.ensure_one()
        self.check_access('read')
        records.check_access('read')
        action = {'type': 'ir.actions.act_window', 'name': title, 'res_model': records._name,
                  'view_mode': 'list,form', 'domain': [('id', 'in', records.ids)],
                  'context': {'create': False}}
        if len(records) == 1:
            action.update(view_mode='form', res_id=records.id)
        return action

    def action_return_invoices(self):
        return self._return_open_records(self.return_related_invoice_ids, _('Facturas relacionadas'))

    def action_return_pickings(self):
        return self._return_open_records(self.return_related_picking_ids, _('Devoluciones de inventario'))

    def action_return_payments(self):
        return self._return_open_records(self.return_related_payment_ids, _('Pagos de devolución'))

    def action_return_credits(self):
        return self._return_open_records(self.return_related_credit_ids, _('Notas de crédito vigentes'))

    def action_return_request(self):
        return self._return_open_records(self.customer_adjustment_id or self.pos_accounting_return_id,
                                         _('Ajuste de origen'))

    def action_return_ticket(self):
        return self._return_open_records(self.pos_accounting_return_id.order_id, _('Ticket original'))

    def action_return_source_entry(self):
        return self._return_open_records(self.pos_return_source_move_id, _('Asiento de venta original'))


class AccountMoveLineReturnNavigation(models.Model):
    _inherit = 'account.move.line'

    return_source_quantity = fields.Float(string='Cantidad original devuelta', digits='Product Unit',
                                          copy=False, readonly=True)
    pos_return_source_line_id = fields.Many2one('pos.order.line', string='Línea del ticket original',
                                               copy=False, readonly=True, check_company=True, ondelete='restrict')
