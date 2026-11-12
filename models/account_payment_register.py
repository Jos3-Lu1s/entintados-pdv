from odoo import _, Command, models, api, fields
from odoo.exceptions import UserError


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    vendor_payment_group_id = fields.Many2one('vendor.payment.group', readonly=True)
    vendor_allocate = fields.Boolean(string='Distribuir importe por factura')
    vendor_allocation_available = fields.Boolean(compute='_compute_vendor_allocation_available')
    vendor_allocation_ids = fields.One2many('vendor.payment.allocation', 'wizard_id', string='Facturas seleccionadas')
    vendor_allocated_amount = fields.Monetary(string='Total asignado', compute='_compute_vendor_allocated_amount')
    vendor_percentage_editable = fields.Boolean(compute='_compute_vendor_percentage_editable')

    @api.depends('line_ids.move_id.move_type', 'line_ids.move_id.state',
                 'line_ids.move_id.currency_id', 'line_ids.move_id.company_id', 'currency_id')
    def _compute_vendor_percentage_editable(self):
        for wizard in self:
            invoices = wizard.line_ids.move_id
            wizard.vendor_percentage_editable = bool(
                invoices and wizard.currency_id and invoices.currency_id == wizard.currency_id
                and len(invoices.company_id) == 1
                and all(invoice.move_type == 'in_invoice' and invoice.state == 'posted' for invoice in invoices)
            )

    def _vendor_pending_total(self):
        self.ensure_one()
        return sum(self.line_ids.move_id.mapped('amount_residual'))

    @api.depends('vendor_percentage_editable', 'line_ids', 'vendor_payment_group_id')
    def _compute_vendor_allocation_available(self):
        for wizard in self:
            invoices = wizard.line_ids.move_id
            wizard.vendor_allocation_available = (
                wizard.vendor_percentage_editable and (len(invoices) > 1 or bool(wizard.vendor_payment_group_id))
            )

    @api.depends('vendor_allocation_ids.amount', 'currency_id')
    def _compute_vendor_allocated_amount(self):
        for wizard in self:
            wizard.vendor_allocated_amount = sum(wizard.vendor_allocation_ids.mapped('amount'))

    @api.onchange('vendor_allocate')
    def _onchange_vendor_allocate(self):
        if self.vendor_allocate and self.vendor_allocation_available:
            self.group_payment = True
            self.installments_mode = 'full'
            self.payment_difference_handling = 'open'
            if not self.vendor_allocation_ids:
                remaining = self.amount
                values = []
                for invoice in self.line_ids.move_id:
                    amount = min(invoice.amount_residual, max(remaining, 0))
                    values.append(Command.create({'invoice_id': invoice.id, 'amount': amount}))
                    remaining -= amount
                self.vendor_allocation_ids = values

    def _create_payments(self):
        group = self.vendor_payment_group_id
        if group:
            group._lock_draft()
            if not self.vendor_allocate or set(self.line_ids.move_id.ids) != set(group._selected_invoices().ids):
                raise UserError(_('La selección de facturas cambió. Vuelve a abrir Pagar desde el grupo.'))
            if self.currency_id != group.currency_id:
                raise UserError(_('Usa la moneda del grupo para registrar los pagos.'))
            method = group._check_payment_method()
            if self.payment_method_line_id != method or self.journal_id != method.journal_id:
                raise UserError(_('La forma de pago del grupo cambió. Vuelve a abrir Pagar.'))
        if self.vendor_percentage_editable:
            invoices = self.line_ids.move_id
            invoices.invalidate_recordset(['amount_residual'])
            if self.currency_id.compare_amounts(self.amount, self._vendor_pending_total()) > 0:
                raise UserError(_('El importe no puede superar la suma de los saldos pendientes de las facturas.'))
        if not self.vendor_allocate:
            return super()._create_payments()

        self.ensure_one()
        if not self.vendor_allocation_available:
            raise UserError(_('Para distribuir el importe, selecciona facturas contabilizadas de la misma compañía y moneda.'))
        if self.payment_difference_handling == 'reconcile' or self.early_payment_discount_mode:
            raise UserError(_('Para distribuir el importe, mantén abierta la diferencia de pago y desactiva el descuento por pronto pago.'))

        invoices = self.line_ids.move_id
        if set(self.vendor_allocation_ids.invoice_id.ids) != set(invoices.ids):
            raise UserError(_('La distribución debe corresponder a las facturas seleccionadas.'))
        payable = invoices.line_ids.filtered(lambda line: line.account_type == 'liability_payable')
        payable.flush_recordset(['amount_residual', 'amount_residual_currency'])
        self.env.cr.execute(
            'SELECT id FROM account_move_line WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(payable.ids)],
        )
        invoices.invalidate_recordset(['amount_residual', 'state'])
        if group:
            group._check_selected_invoices(invoices)
        currency = self.currency_id
        amounts = {}
        expected = {}
        for line in self.vendor_allocation_ids:
            invoice = line.invoice_id
            amount = currency.round(line.amount)
            if invoice.state != 'posted' or amount < 0 or currency.compare_amounts(amount, invoice.amount_residual) > 0:
                raise UserError(_('Revisa el monto asignado a %(invoice)s. Debe estar entre cero y su saldo pendiente.',
                                  invoice=invoice.display_name))
            if amount:
                if group and self.country_code == 'MX' and not line.sat_payment_method_id:
                    raise UserError(_('Selecciona la forma de pago del SAT para %(invoice)s.',
                                      invoice=invoice.display_name))
                amounts[invoice.id] = amount
                expected[invoice.id] = invoice.amount_residual - amount
        total = sum(amounts.values())
        if not amounts or currency.compare_amounts(total, self.amount) > 0:
            raise UserError(_('Asigna un importe positivo a las facturas sin superar el importe del encabezado.'))
        payment_group = group
        if not payment_group:
            payment_group = self.env['vendor.payment.group'].create({
                'company_id': invoices.company_id.id,
                'currency_id': currency.id,
                'payment_date': self.payment_date,
                'payment_method_line_id': self.payment_method_line_id.id,
                'line_ids': [Command.create({
                    'invoice_id': invoice_id,
                    'selected': True,
                    'amount_to_pay': amount,
                    'payment_method_line_id': self.payment_method_line_id.id,
                    'sat_payment_method_id': self.l10n_mx_edi_payment_method_id.id,
                }) for invoice_id, amount in amounts.items()],
            })
        if group:
            group.payment_date = self.payment_date
            fiscal_methods = {line.invoice_id.id: line.sat_payment_method_id for line in self.vendor_allocation_ids}
            for line in group.line_ids.filtered('selected'):
                if line.invoice_id.id in amounts:
                    line.write({
                        'amount_to_pay': amounts[line.invoice_id.id],
                        'payment_method_line_id': self.payment_method_line_id.id,
                        'sat_payment_method_id': fiscal_methods[line.invoice_id.id].id,
                    })
                else:
                    line.unlink()
        payments = super()._create_payments()
        if currency.compare_amounts(sum(payments.mapped('amount')), total) or any(
            currency.compare_amounts(invoice.amount_residual, expected[invoice.id])
            for invoice in invoices if invoice.id in expected
        ):
            raise UserError(_('No se pudo aplicar la distribución exacta. Revisa la cuenta de pagos pendientes del diario.'))
        payment_group._mark_registered(payments)
        if not group:
            self.vendor_payment_group_id = payment_group
        return payments

    def _init_payments(self, to_process, edit_mode=False):
        if self.vendor_allocate:
            distributed = []
            included = self.env['account.move']
            group_full_payments = self.vendor_payment_group_id and self.group_payment
            amounts = {
                line.invoice_id: self.currency_id.round(line.amount)
                for line in self.vendor_allocation_ids
            }
            fiscal_methods = {
                line.invoice_id: line.sat_payment_method_id.id
                for line in self.vendor_allocation_ids
            } if self.vendor_payment_group_id and self.country_code == 'MX' else {}
            for item in to_process:
                full_payments = {}
                for invoice, payable in item['to_reconcile'].grouped('move_id').items():
                    amount = amounts.get(invoice)
                    if not amount:
                        continue
                    values = dict(item['create_vals'], amount=amount)
                    if invoice in fiscal_methods:
                        values['l10n_mx_edi_payment_method_id'] = fiscal_methods[invoice]
                    if values.get('write_off_line_vals') or values.get('force_balance'):
                        raise UserError(_('Revisa los descuentos por pronto pago o ajustes antes de distribuir el importe.'))
                    included |= invoice
                    if group_full_payments and self.currency_id.compare_amounts(amount, invoice.amount_residual) == 0:
                        fiscal_method = values.get('l10n_mx_edi_payment_method_id')
                        if fiscal_method in full_payments:
                            payment = full_payments[fiscal_method]
                            payment['to_reconcile'] |= payable
                            payment['create_vals']['amount'] = self.currency_id.round(
                                payment['create_vals']['amount'] + amount
                            )
                        else:
                            full_payments[fiscal_method] = dict(item, to_reconcile=payable, create_vals=values)
                    else:
                        distributed.append(dict(item, to_reconcile=payable, create_vals=values))
                distributed.extend(full_payments.values())
            if set(included) != {invoice for invoice, amount in amounts.items() if amount > 0}:
                raise UserError(_('Hay facturas fuera del pago. Revisa los saldos, vencimientos y cuentas bancarias.'))
            to_process[:] = distributed
        if self.vendor_payment_group_id:
            group_method = self.vendor_payment_group_id.payment_method_line_id
            for item in to_process:
                values = item['create_vals']
                method = self.env['account.payment.method.line'].browse(values['payment_method_line_id'])
                if method != group_method:
                    raise UserError(_('El asistente no pudo aplicar la forma de pago seleccionada en el grupo.'))
                journal = method.journal_id
                account = journal.default_account_id
                if (values['payment_type'] == 'outbound' and values['partner_type'] == 'supplier'
                        and journal.type == 'cash' and method.code == 'manual' and not method.payment_account_id
                        and account.active and account.account_type == 'asset_cash'):
                    values['force_outstanding_account_id'] = account.id
        return super()._init_payments(to_process, edit_mode=edit_mode)

    distributed_notes = fields.Boolean(related='partner_id.distributed_notes', readonly=True)
    payment_percentage = fields.Float(
        string="Porcentaje de pago", compute='_compute_discount_percentage',
        inverse='_apply_payment_percentage',
        help="Porcentaje del saldo pendiente que se pagará.",
    )
    discount_percentage = fields.Float(
        string="Porcentaje restante",
        compute='_compute_discount_percentage',
        help="Porcentaje del saldo pendiente que quedará abierto después de pagar el importe indicado.",
    )
    
    @api.depends('vendor_percentage_editable', 'amount', 'line_ids.move_id.amount_residual')
    def _compute_discount_percentage(self):
        for wizard in self:
            balance = wizard._vendor_pending_total() if wizard.vendor_percentage_editable else 0
            wizard.payment_percentage = wizard.amount / balance if balance else 0
            wizard.discount_percentage = max(1 - wizard.payment_percentage, 0) if balance else 0

    @api.onchange('payment_percentage')
    def _onchange_payment_percentage(self):
        self._apply_payment_percentage()
        self._compute_discount_percentage()

    def _apply_payment_percentage(self):
        for wizard in self.filtered('vendor_percentage_editable'):
            percentage = min(max(wizard.payment_percentage, 0), 1)
            amount = wizard.currency_id.round(wizard._vendor_pending_total() * percentage)
            wizard.custom_user_amount = amount
            wizard.custom_user_currency_id = wizard.currency_id
            wizard.amount = amount
            if wizard.id:
                wizard.invalidate_recordset(['payment_percentage', 'discount_percentage'], flush=False)

    @api.onchange('amount')
    def _onchange_amount(self):
        if self.vendor_percentage_editable:
            pending = self._vendor_pending_total()
            if self.currency_id.compare_amounts(self.amount, pending) > 0:
                self.amount = pending
            if self.vendor_allocate and not self.can_edit_wizard:
                self.custom_user_amount = self.amount
                self.custom_user_currency_id = self.currency_id
        return super()._onchange_amount()

    @api.depends('can_edit_wizard', 'source_amount', 'source_amount_currency', 'source_currency_id',
                 'company_id', 'currency_id', 'payment_date', 'installments_mode',
                 'vendor_percentage_editable', 'line_ids.move_id.amount_residual')
    def _compute_amount(self):
        super()._compute_amount()
        for wizard in self:
            if (wizard.vendor_percentage_editable and wizard.journal_id
                    and wizard.payment_date and not wizard.custom_user_amount):
                wizard.amount = wizard.currency_id.round(wizard._vendor_pending_total())


class VendorPaymentAllocation(models.TransientModel):
    _name = 'vendor.payment.allocation'
    _description = 'Distribución del pago por factura de proveedor'

    wizard_id = fields.Many2one('account.payment.register', required=True, ondelete='cascade')
    invoice_id = fields.Many2one('account.move', string='Factura', required=True, readonly=True, ondelete='cascade')
    partner_id = fields.Many2one(related='invoice_id.partner_id', string='Proveedor')
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    balance = fields.Monetary(string='Saldo pendiente', related='invoice_id.amount_residual')
    amount = fields.Monetary(string='Monto a pagar')
    sat_payment_method_id = fields.Many2one('l10n_mx_edi.payment.method', string='Forma de pago')

    _unique_invoice = models.Constraint('UNIQUE(wizard_id, invoice_id)', 'La factura ya está en la distribución.')

    @api.onchange('amount')
    def _onchange_amount(self):
        if self.currency_id and self.currency_id.compare_amounts(self.amount, self.balance) > 0:
            self.amount = self.balance
