from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError


class CustomerAccountAdjustment(models.Model):
    _name = 'customer.account.adjustment'
    _description = 'Ajuste contable de cliente'
    _check_company_auto = True
    _order = 'date desc, id desc'

    name = fields.Char(string='Folio', required=True, readonly=True, copy=False, default=lambda self: _('New'))
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    partner_id = fields.Many2one('res.partner', string='Cliente', required=True, check_company=True)
    currency_id = fields.Many2one('res.currency', string='Moneda', required=True,
                                  default=lambda self: self.env.company.currency_id)
    journal_id = fields.Many2one('account.journal', string='Diario de ventas', required=True,
                                 compute='_compute_journal', store=True, readonly=False, precompute=True,
                                 check_company=True, domain="[('type', '=', 'sale'), ('company_id', '=', company_id)]")
    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today)
    reason = fields.Char(string='Motivo', required=True)
    state = fields.Selection([('draft', 'Borrador'), ('generated', 'Notas generadas'), ('cancel', 'Cancelado')],
                             default='draft', required=True, readonly=True, copy=False)
    line_ids = fields.One2many('customer.account.adjustment.line', 'adjustment_id', string='Facturas pagadas', copy=False)
    credit_ids = fields.One2many('account.move', 'customer_adjustment_id', string='Notas de crédito', readonly=True)
    payment_ids = fields.One2many('account.payment', 'customer_adjustment_id', string='Pagos de devolución', readonly=True)
    credit_count = fields.Integer(compute='_compute_totals')
    invoice_count = fields.Integer(compute='_compute_totals')
    amount_total = fields.Monetary(string='Total a revertir', compute='_compute_totals')

    @api.depends('line_ids.selected', 'line_ids.amount', 'line_ids.invoice_ids', 'credit_ids')
    def _compute_totals(self):
        for adjustment in self:
            selected = adjustment.line_ids.filtered('selected')
            adjustment.invoice_count = len(selected.invoice_ids)
            adjustment.amount_total = sum(selected.mapped('amount'))
            adjustment.credit_count = len(adjustment.credit_ids)

    @api.depends('company_id', 'currency_id')
    def _compute_journal(self):
        for adjustment in self:
            journal = adjustment.journal_id
            if (journal and journal.type == 'sale' and journal.company_id == adjustment.company_id
                    and (not journal.currency_id or journal.currency_id == adjustment.currency_id)):
                continue
            domain = [('type', '=', 'sale'), ('company_id', '=', adjustment.company_id.id)]
            adjustment.journal_id = self.env['account.journal'].search(
                domain + [('currency_id', '=', adjustment.currency_id.id)], limit=1
            ) or self.env['account.journal'].search(domain + [('currency_id', '=', False)], limit=1)

    def _invoice_domain(self):
        self.ensure_one()
        return [('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
                ('payment_state', '=', 'paid'), ('amount_total', '>', 0),
                ('partner_id', '=', self.partner_id.id), ('company_id', '=', self.company_id.id),
                ('currency_id', '=', self.currency_id.id), ('l10n_mx_edi_cfdi_state', '!=', 'cancel')]

    @api.onchange('partner_id', 'company_id', 'currency_id')
    def _onchange_scope(self):
        self.line_ids = [Command.clear()]
        if self.partner_id:
            eligible = self.env['account.move'].search(self._invoice_domain(), order='invoice_date desc, id desc')
            seen, commands = set(), []
            for invoice in eligible:
                invoices = self._split_invoices(invoice)
                key = tuple(sorted(invoices.ids))
                if key in seen or not set(invoices.ids).issubset(eligible.ids):
                    continue
                seen.add(key)
                commands.append(Command.create({'invoice_id': invoices.sorted('id')[0].id,
                                                'selected': False, 'amount': 0}))
            self.line_ids = commands

    @api.model
    def _split_invoices(self, invoice):
        """Use the explicit POS relationship, never invoice names/references."""
        orders = self.env['pos.order'].sudo().search([
            ('company_id', '=', invoice.company_id.id),
            '|', ('split_invoice_ids', 'in', invoice.ids), ('account_move', 'in', invoice.ids),
        ])
        groups = {tuple(sorted((order.split_invoice_ids | order.account_move).ids)) for order in orders}
        if len(groups) > 1:
            raise UserError(_('La factura %s pertenece a más de una división del POS. Revisa sus relaciones antes de ajustar.')
                            % invoice.display_name)
        invoices = self.env['account.move'].browse(next(iter(groups))) if groups else invoice
        invoices.check_access('read')
        return invoices

    def _credit_allocations(self, selected):
        """The entered amount is the total for the whole split invoice group."""
        grouped = {}
        for line in selected:
            if self.currency_id.compare_amounts(line.amount, 0) <= 0:
                raise UserError(_('El importe a revertir debe ser mayor que cero.'))
            invoices = self._split_invoices(line.invoice_id).sorted('id')
            key = tuple(invoices.ids)
            grouped[key] = grouped.get(key, 0) + line.amount
        allocations = []
        for ids, amount in grouped.items():
            invoices = self.env['account.move'].browse(ids)
            total = sum(invoices.mapped('amount_total'))
            if total <= 0 or any(invoice.amount_total <= 0 for invoice in invoices):
                raise UserError(_('Todas las facturas de la división deben tener un total positivo.'))
            cumulative, allocated = 0, 0
            for invoice in invoices:
                cumulative += invoice.amount_total
                target = self.currency_id.round(amount * cumulative / total)
                share = self.currency_id.round(target - allocated)
                allocations.append((invoice, share))
                allocated = target
        return allocations

    def action_reload_invoices(self):
        self.ensure_one()
        self._check_editable()
        self._onchange_scope()
        return True

    def _check_editable(self):
        if any(adjustment.state != 'draft' or adjustment.credit_ids.filtered(lambda credit: credit.state != 'cancel') for adjustment in self):
            raise UserError(_('Solo puedes modificar un ajuste en borrador sin notas de crédito.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('state', 'draft') != 'draft':
                raise UserError(_('Los ajustes deben crearse en borrador.'))
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(self._name) or _('New')
        return super().create(vals_list)

    def write(self, vals):
        if 'state' in vals:
            raise UserError(_('Usa los botones del ajuste para cambiar su etapa y actualizar los documentos relacionados.'))
        if {'partner_id', 'company_id', 'currency_id', 'journal_id', 'date', 'reason', 'line_ids', 'state'}.intersection(vals):
            self._check_editable()
        return super().write(vals)

    def unlink(self):
        self._check_editable()
        if any(adjustment.credit_ids or adjustment.payment_ids for adjustment in self):
            raise UserError(_('No puedes eliminar un ajuste con historial de notas o pagos.'))
        return super().unlink()

    def action_cancel(self):
        self.check_access('write')
        for adjustment in self.sorted('id'):
            self.env['res.company']._with_locked_records(adjustment)
            adjustment.invalidate_recordset(['state', 'credit_ids', 'payment_ids'])
            if adjustment.state == 'cancel':
                continue
            credits = adjustment.credit_ids
            self.env['res.company']._with_locked_records(credits.sorted('id'))
            payments = adjustment.payment_ids | credits._get_reconciled_payments().filtered(
                lambda payment: payment.payment_type == 'outbound')
            self.env['res.company']._with_locked_records(payments.sorted('id'))
            # Validate the entire cancellation before changing any document.
            payments._check_adjustment_cancellation(credits, adjustment)
            # Native cancellation keeps fiscal cancellation requests, lock
            # dates and journal restrictions in force. RPC rollback is atomic.
            for credit in credits.filtered(lambda credit: credit.state != 'cancel'):
                credit.button_cancel()
            for payment in payments.filtered(lambda payment: payment.state != 'canceled'):
                payment.customer_adjustment_id = adjustment
                payment.action_cancel()
            super(CustomerAccountAdjustment, adjustment).write({'state': 'cancel'})
        return True

    def action_draft(self):
        self.check_access('write')
        for adjustment in self:
            if adjustment.state != 'cancel':
                raise UserError(_('Solo puedes restablecer un ajuste cancelado.'))
            if (adjustment.credit_ids.filtered(lambda credit: credit.state != 'cancel')
                    or adjustment.payment_ids.filtered(lambda payment: payment.state != 'canceled')):
                raise UserError(_('Todas las notas y pagos relacionados deben estar cancelados antes de restablecer el ajuste.'))
        super(CustomerAccountAdjustment, self).write({'state': 'draft'})
        return True

    def action_open_credits(self):
        self.ensure_one()
        return self.env['account.move']._action_open_moves(self.credit_ids, _('Notas de crédito'))

    def _original_payment_distribution(self, invoice):
        """Amounts collected per bank/cash journal, in invoice currency."""
        weights = {}
        receivable = invoice.line_ids.filtered(lambda line: line.account_id.account_type == 'asset_receivable')
        for partial in receivable.matched_credit_ids:
            journal = partial.credit_move_id.journal_id
            if journal.type in ('bank', 'cash'):
                weights[journal.id] = weights.get(journal.id, 0) + partial.debit_amount_currency
        if not weights:
            # POS collections may be reconciled through a consolidated entry,
            # rather than an account.payment linked to each split invoice.
            orders = self.env['pos.order'].sudo().search([
                ('company_id', '=', invoice.company_id.id),
                '|', ('account_move', '=', invoice.id), ('split_invoice_ids', 'in', invoice.ids),
            ])
            for payment in orders.payment_ids.filtered(lambda payment: payment.amount > 0):
                journal = payment.payment_method_id.journal_id
                if journal.type in ('bank', 'cash'):
                    weights[journal.id] = weights.get(journal.id, 0) + payment.amount
        if not weights or sum(weights.values()) <= 0:
            raise UserError(_('No se pudo identificar un diario de banco o caja del pago original de %s. Revisa sus pagos antes de generar el ajuste.')
                            % invoice.display_name)
        result = []
        for journal_id, weight in sorted(weights.items()):
            journal = self.env['account.journal'].browse(journal_id)
            journal.check_access('read')
            method = journal.outbound_payment_method_line_ids.filtered(
                lambda method: method.payment_method_id.code == 'manual')[:1]
            if not method:
                raise UserError(_('Configura un método de pago saliente manual en el diario %s para devolver el importe automáticamente.')
                                % journal.display_name)
            if not (method.payment_account_id or journal.default_account_id):
                raise UserError(_('Configura la cuenta de pagos salientes o la cuenta de liquidez del diario %s antes de generar el ajuste.')
                                % journal.display_name)
            result.append((journal, method, weight))
        return result

    def _pay_credit(self, credit, distribution):
        self.ensure_one()
        total = sum(weight for journal, method, weight in distribution)
        cumulative, allocated = 0, 0
        amount = credit.amount_residual
        for journal, method, weight in distribution:
            cumulative += weight
            target = credit.currency_id.round(amount * cumulative / total)
            share = credit.currency_id.round(target - allocated)
            allocated = target
            if credit.currency_id.is_zero(share):
                continue
            wizard = self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=credit.ids,
                # Odoo 19 permits payments without an entry when the method has
                # no outstanding account. These automatic refunds must create
                # an entry: use this journal's liquidity account in that case.
                customer_adjustment_refund_account_id=(method.payment_account_id or journal.default_account_id).id,
            ).create({
                'journal_id': journal.id, 'payment_method_line_id': method.id,
                'payment_date': self.date, 'currency_id': credit.currency_id.id,
                'amount': share, 'custom_user_amount': share,
                'custom_user_currency_id': credit.currency_id.id,
                'payment_difference_handling': 'open', 'group_payment': True,
            })
            payments = wizard._create_payments()
            if not payments or any(payment.move_id.state != 'posted' for payment in payments):
                raise UserError(_('No fue posible contabilizar el pago de %s.') % credit.display_name)
            payments.write({'customer_adjustment_id': self.id, 'customer_adjustment_credit_id': credit.id})
        if not credit.currency_id.is_zero(credit.amount_residual):
            raise UserError(_('No fue posible conciliar completamente la nota %s con su pago.') % credit.display_name)

    def action_pay_credits(self):
        """Also settle notes generated before automatic payment was enabled."""
        self.ensure_one()
        self.check_access('write')
        if self.state == 'cancel':
            raise UserError(_('No puedes pagar notas de un ajuste cancelado.'))
        credits = self.credit_ids.filtered(lambda credit: credit.state == 'posted'
                                           and credit.l10n_mx_edi_cfdi_state != 'cancel'
                                           and credit.amount_residual > 0)
        self.env['res.company']._with_locked_records(credits.sorted('id'))
        credits.invalidate_recordset()
        for credit in credits:
            if credit.amount_residual > 0:
                self._pay_credit(credit, self._original_payment_distribution(credit.reversed_entry_id))
        return self.action_open_credits()

    def action_create_credits(self):
        self.ensure_one()
        self.check_access('write')
        # Serialize simultaneous clicks and adjustments against the same invoice.
        self.env['res.company']._with_locked_records(self)
        self.invalidate_recordset(['credit_ids', 'state'])
        if self.state == 'cancel':
            raise UserError(_('Restablece el ajuste a borrador antes de generar nuevas notas.'))
        if self.credit_ids.filtered(lambda credit: credit.state != 'cancel'):
            return self.action_pay_credits()
        self._check_editable()
        selected = self.line_ids.filtered('selected')
        if not selected:
            raise UserError(_('Selecciona al menos una factura.'))
        invoices = selected.invoice_id
        if len(invoices) != len(selected):
            raise UserError(_('No puedes repetir una factura en el ajuste.'))
        allocations = self._credit_allocations(selected)
        invoices = self.env['account.move'].browse([invoice.id for invoice, amount in allocations])
        if self.company_id not in self.env.companies:
            raise UserError(_('La compañía no está habilitada en la sesión.'))
        if self.journal_id.type != 'sale' or self.journal_id.company_id != self.company_id:
            raise UserError(_('Selecciona un diario de ventas de la misma compañía.'))
        if self.journal_id.currency_id and self.journal_id.currency_id != self.currency_id:
            raise UserError(_('La moneda del diario debe coincidir con el ajuste.'))
        self.env['res.company']._with_locked_records(invoices.sorted('id'))
        invoices.invalidate_recordset()
        valid = self.env['account.move'].search(self._invoice_domain() + [('id', 'in', invoices.ids)])
        if invoices != valid and set(invoices.ids) != set(valid.ids):
            raise UserError(_('Las facturas seleccionadas deben estar pagadas y contabilizadas, y corresponder al cliente, compañía y moneda del ajuste.'))
        # Draft credits reserve the amount too, including returns and manual notes.
        previous = self.env['account.move'].search([
            ('reversed_entry_id', 'in', invoices.ids), ('move_type', '=', 'out_refund'),
            ('state', 'in', ['draft', 'posted']), ('l10n_mx_edi_cfdi_state', '!=', 'cancel'),
        ])
        distributions = {}
        for invoice, amount in allocations:
            credited = sum(previous.filtered(lambda credit: credit.reversed_entry_id == invoice).mapped('amount_total'))
            available = max(invoice.amount_total - credited, 0)
            if self.currency_id.compare_amounts(amount, available) > 0:
                raise UserError(_('El importe de %s debe ser mayor que cero y no superar el total pendiente de revertir (%s).')
                                % (invoice.display_name, available))
            if not self.currency_id.is_zero(amount):
                distributions[invoice.id] = self._original_payment_distribution(invoice)
        for invoice, amount in allocations:
            if self.currency_id.is_zero(amount):
                continue
            ratio = amount / invoice.amount_total
            commands = []
            for line in invoice.invoice_line_ids.filtered(lambda line: line.display_type == 'product'):
                values = line.copy_data({'product_id': invoice._reajuste_product().id, 'name': 'Reajuste',
                                         'product_uom_id': invoice._reajuste_product().uom_id.id,
                                         'quantity': 1, 'price_unit': line.price_unit * line.quantity * ratio,
                                         'stock_return_source_line_id': line.id})[0]
                for key in ('move_id', 'purchase_line_id', 'sale_line_ids'):
                    values.pop(key, None)
                commands.append(Command.create(values))
            credit = invoice._reverse_moves(default_values_list=[{
                'date': self.date, 'invoice_date': self.date, 'journal_id': self.journal_id.id,
                'auto_post': 'no', 'ref': '%s: %s' % (self.name, self.reason),
                'customer_adjustment_id': self.id, 'line_ids': commands,
            }], cancel=False)
            difference = self.currency_id.round(amount - credit.amount_total)
            if difference:
                # Price/tax precision can leave a few cents when distributing
                # an arbitrary tax-inclusive amount. Keep that difference visible.
                limit = self.currency_id.rounding * (2 * len(commands) + 1)
                if abs(difference) > limit + self.currency_id.rounding / 2:
                    raise UserError(_('Los impuestos o redondeos de %s no permiten distribuir el importe solicitado. Ajusta el importe.')
                                    % invoice.display_name)
                source = invoice.invoice_line_ids.filtered(lambda line: line.display_type == 'product')[:1]
                credit.write({'invoice_line_ids': [Command.create({
                    'name': 'Reajuste', 'product_id': invoice._reajuste_product().id,
                    'product_uom_id': invoice._reajuste_product().uom_id.id, 'account_id': source.account_id.id,
                    'quantity': 1, 'price_unit': difference, 'tax_ids': [Command.clear()],
                })]})
            if self.currency_id.compare_amounts(credit.amount_total, amount):
                raise UserError(_('No fue posible obtener el importe exacto solicitado para %s.') % invoice.display_name)
            # Register a separate outbound payment; the original collection
            # remains reconciled with the original invoice.
            credit.action_post()
            self._pay_credit(credit, distributions[invoice.id])
        super(CustomerAccountAdjustment, self).write({'state': 'generated'})
        return self.action_open_credits()


class CustomerAccountAdjustmentLine(models.Model):
    _name = 'customer.account.adjustment.line'
    _description = 'Factura de ajuste contable de cliente'
    _check_company_auto = True

    adjustment_id = fields.Many2one('customer.account.adjustment', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='adjustment_id.company_id', store=True)
    selected = fields.Boolean(string='Incluir', default=False)
    invoice_id = fields.Many2one('account.move', string='Factura', required=True, check_company=True)
    currency_id = fields.Many2one(related='adjustment_id.currency_id')
    invoice_ids = fields.Many2many('account.move', compute='_compute_invoice_group', string='Facturas relacionadas')
    invoice_total = fields.Monetary(compute='_compute_invoice_group', string='Importe total')
    amount = fields.Monetary(string='Cantidad a revertir (con impuestos)')

    @api.depends('invoice_id', 'invoice_id.amount_total')
    def _compute_invoice_group(self):
        for line in self:
            invoices = line.adjustment_id._split_invoices(line.invoice_id) if line.invoice_id else self.env['account.move']
            line.invoice_ids = invoices
            line.invoice_total = sum(invoices.mapped('amount_total'))

    @api.model_create_multi
    def create(self, vals_list):
        self.env['customer.account.adjustment'].browse([
            vals['adjustment_id'] for vals in vals_list if vals.get('adjustment_id')])._check_editable()
        return super().create(vals_list)

    def write(self, vals):
        self.adjustment_id._check_editable()
        if vals.get('adjustment_id'):
            self.env['customer.account.adjustment'].browse(vals['adjustment_id'])._check_editable()
        return super().write(vals)

    def unlink(self):
        self.adjustment_id._check_editable()
        return super().unlink()


class AccountMoveCustomerAdjustment(models.Model):
    _inherit = 'account.move'

    customer_adjustment_id = fields.Many2one('customer.account.adjustment', string='Ajuste contable de origen',
                                             readonly=True, copy=False, check_company=True, ondelete='restrict', index=True)

    def button_cancel(self):
        credits = self.filtered(lambda move: move.customer_adjustment_id and move.move_type == 'out_refund')
        payments = self.env['account.payment']
        for adjustment in credits.customer_adjustment_id:
            notes = credits.filtered(lambda move: move.customer_adjustment_id == adjustment)
            related = notes._get_reconciled_payments().filtered(lambda payment: payment.payment_type == 'outbound')
            related |= self.env['account.payment'].search([('customer_adjustment_credit_id', 'in', notes.ids)])
            self.env['res.company']._with_locked_records(related.sorted('id'))
            related._check_adjustment_cancellation(notes, adjustment)
            # Keep the exact note even after native cancellation removes reconciliation.
            for note in notes:
                note_payments = related & note._get_reconciled_payments()
                note_payments.write({'customer_adjustment_id': adjustment.id,
                                     'customer_adjustment_credit_id': note.id})
            payments |= related
        result = super().button_cancel()
        active_payments = payments.filtered(lambda payment: payment.state != 'canceled')
        if active_payments:
            active_payments.action_cancel()
        return result


class AccountPaymentCustomerAdjustment(models.Model):
    _inherit = 'account.payment'

    customer_adjustment_id = fields.Many2one('customer.account.adjustment', string='Ajuste contable de origen',
                                             readonly=True, copy=False, check_company=True, ondelete='restrict', index=True)
    customer_adjustment_credit_id = fields.Many2one(
        'account.move', string='Nota de crédito del ajuste', readonly=True, copy=False,
        check_company=True, ondelete='restrict', index=True)

    def _check_adjustment_cancellation(self, credits, adjustment):
        for payment in self.filtered(lambda payment: payment.state != 'canceled'):
            related = payment.move_id._get_reconciled_amls().move_id.filtered(
                lambda move: move.is_invoice(include_receipts=True))
            if (related - credits or payment.payment_type != 'outbound'
                    or (payment.customer_adjustment_id and payment.customer_adjustment_id != adjustment)):
                raise UserError(_('El pago %s está relacionado con otros documentos. Separa su conciliación antes de cancelar este ajuste.')
                                % payment.display_name)
            if payment.move_id._get_reconciled_statement_lines():
                raise UserError(_('El pago %s ya está conciliado con un movimiento bancario. Revisa esa conciliación antes de cancelar el ajuste.')
                                % payment.display_name)


class AccountPaymentRegisterCustomerAdjustment(models.TransientModel):
    _inherit = 'account.payment.register'

    def _init_payments(self, to_process, edit_mode=False):
        account_id = self.env.context.get('customer_adjustment_refund_account_id')
        payments = super()._init_payments(to_process, edit_mode=edit_mode)
        if account_id:
            payments.filtered(lambda payment: not payment.outstanding_account_id).write({
                'outstanding_account_id': account_id,
            })
        return payments
