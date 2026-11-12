from io import BytesIO

import xlsxwriter

from odoo import _, api, fields, models, Command
from odoo.exceptions import UserError


class VendorPaymentGroup(models.Model):
    _name = 'vendor.payment.group'
    _description = 'Pago agrupado de proveedor'
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(string='Folio', required=True, readonly=True, copy=False, default=lambda self: _('Nuevo'))
    company_id = fields.Many2one('res.company', string='Compañía', required=True, default=lambda self: self.env.company)
    partner_id = fields.Many2one('res.partner', string='Proveedor', check_company=True)
    currency_id = fields.Many2one('res.currency', string='Moneda', required=True, default=lambda self: self.env.company.currency_id)
    payment_method_line_id = fields.Many2one('account.payment.method.line', string='Método del diario', ondelete='restrict')
    country_code = fields.Char(related='company_id.account_fiscal_country_id.code')
    payment_date = fields.Date(string='Fecha de pago', default=fields.Date.context_today)
    previous_group_id = fields.Many2one('vendor.payment.group', string='Grupo anterior', readonly=True,
                                        copy=False, check_company=True, ondelete='restrict')
    has_pending_balance = fields.Boolean(compute='_compute_has_pending_balance')
    line_ids = fields.One2many('vendor.payment.group.line', 'group_id', string='Facturas pendientes', copy=False)
    payment_ids = fields.Many2many('account.payment', string='Pagos', readonly=True, copy=False, check_company=True)
    state = fields.Selection([('draft', 'Borrador'), ('registered', 'Registrado')], default='draft', required=True, readonly=True, copy=False)
    invoice_count = fields.Integer(string='Facturas', compute='_compute_counts')
    payment_count = fields.Integer(string='Pagos', compute='_compute_counts')
    amount_total = fields.Monetary(string='Total a pagar', compute='_compute_counts')

    _unique_name = models.Constraint('UNIQUE(name)', 'El folio del pago agrupado debe ser único.')

    def _selected_invoices(self):
        return self.line_ids.filtered('selected').invoice_id

    @api.depends('state', 'line_ids.selected', 'line_ids.invoice_id.amount_residual', 'line_ids.invoice_id.payment_state')
    def _compute_has_pending_balance(self):
        for group in self:
            group.has_pending_balance = group.state == 'registered' and bool(
                group._selected_invoices().filtered_domain(group._invoice_domain())
            )

    @api.depends('line_ids.selected', 'line_ids.amount_to_pay', 'payment_ids')
    def _compute_counts(self):
        for group in self:
            lines = group.line_ids.filtered('selected')
            group.invoice_count = len(lines.invoice_id)
            group.payment_count = len(group.payment_ids)
            group.amount_total = sum(group.currency_id.round(line.amount_to_pay)
                                     for line in lines) if group.currency_id else 0

    def _invoice_domain(self):
        self.ensure_one()
        domain = [
            ('move_type', '=', 'in_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ['not_paid', 'partial']),
            ('amount_residual', '>', 0),
            ('company_id', '=', self.company_id.id),
            ('currency_id', '=', self.currency_id.id),
        ]
        if self.previous_group_id:
            domain.append(('id', 'in', self.previous_group_id._selected_invoices().ids))
        return domain

    def _default_bank_payment_method(self):
        self.ensure_one()
        return self.env['account.payment.method.line'].search([
            ('payment_type', '=', 'outbound'),
            ('code', '=', 'manual'),
            ('journal_id.type', '=', 'bank'),
            ('journal_id.company_id', '=', self.company_id.id),
            ('journal_id.currency_id', 'in', [False, self.currency_id.id]),
            ('payment_account_id', '!=', False),
        ], order='sequence, id', limit=1)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if {'line_ids', 'payment_method_line_id'}.intersection(fields_list) - values.keys():
            group = self.new({
                'company_id': values.get('company_id') or self.env.company.id,
                'currency_id': values.get('currency_id') or self.env.company.currency_id.id,
                'previous_group_id': values.get('previous_group_id'),
                'line_ids': [],
            })
            if 'line_ids' in fields_list and 'line_ids' not in values:
                values['line_ids'] = group._pending_invoice_commands()
            if 'payment_method_line_id' in fields_list and 'payment_method_line_id' not in values:
                values['payment_method_line_id'] = group._default_bank_payment_method().id
        return values

    def _pending_invoice_commands(self):
        self.ensure_one()
        previous = {line.invoice_id.id: line for line in self.line_ids}
        invoices = self.env['account.move'].search(self._invoice_domain(), order='invoice_date_due, id')
        if not invoices:
            return []
        empty_line = self.env['vendor.payment.group.line']
        return [
            Command.create({
                'invoice_id': invoice.id,
                'selected': bool(previous.get(invoice.id) and previous[invoice.id].selected),
                'amount_to_pay': min(previous.get(invoice.id, empty_line).amount_to_pay or invoice.amount_residual,
                                     invoice.amount_residual),
                'sat_payment_method_id': previous.get(invoice.id, empty_line).sat_payment_method_id.id,
            })
            for invoice in invoices
        ]

    def _refresh_invoices(self):
        self.line_ids = [Command.clear(), *self._pending_invoice_commands()]

    @api.onchange('company_id', 'currency_id')
    def _onchange_scope(self):
        self._refresh_invoices()
        method = self.payment_method_line_id
        if not method or method.journal_id.company_id != self.company_id or (
            method.journal_id.currency_id and method.journal_id.currency_id != self.currency_id
        ):
            self.payment_method_line_id = self._default_bank_payment_method()

    def action_refresh_invoices(self):
        self.ensure_one()
        self._lock_draft()
        self._refresh_invoices()

    def action_select_all(self):
        self.ensure_one()
        self._lock_draft()
        self.line_ids.write({'selected': True})

    def action_deselect_all(self):
        self.ensure_one()
        self._lock_draft()
        self.line_ids.write({'selected': False})

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env.ref('entintados_pdv.seq_vendor_payment_group')
        return super().create([
            dict(vals, name=sequence.next_by_id(), state='draft', payment_ids=[])
            for vals in vals_list
        ])

    def write(self, vals):
        if {'name', 'state', 'payment_ids'} & vals.keys():
            raise UserError(_('El folio, el estado y los pagos vinculados se asignan automáticamente.'))
        self._lock_draft()
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_draft(self):
        self._lock_draft()

    def _lock_draft(self):
        if not self:
            return
        self.check_access('write')
        self.env.cr.execute('SELECT id FROM vendor_payment_group WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(self.ids)])
        self.invalidate_recordset(['state', 'line_ids'])
        if any(group.state != 'draft' for group in self):
            raise UserError(_('El pago agrupado ya está registrado.'))

    def _check_selected_invoices(self, invoices):
        if not invoices:
            raise UserError(_('Selecciona al menos una factura pendiente.'))
        invoices.invalidate_recordset(['amount_residual', 'payment_state'])
        if invoices - self.env['account.move'].search(self._invoice_domain() + [('id', 'in', invoices.ids)]):
            raise UserError(_('Alguna factura ya no está disponible para pagar. Actualiza la lista de facturas.'))

    def action_open_payment_wizard(self):
        self.ensure_one()
        self._lock_draft()
        lines = self.line_ids.filtered('selected')
        invoices = lines.invoice_id
        self._check_selected_invoices(invoices)
        action = invoices.action_register_payment()
        method = self._check_payment_method()
        allocations = []
        amount = 0
        for line in lines:
            line_amount = self.currency_id.round(min(line.amount_to_pay, line.invoice_id.amount_residual))
            allocations.append(Command.create({
                'invoice_id': line.invoice_id.id,
                'amount': line_amount,
                'sat_payment_method_id': line.sat_payment_method_id.id,
            }))
            amount += line_amount
        amount = self.currency_id.round(amount)
        values = {
            'vendor_payment_group_id': self.id,
            'vendor_allocate': True,
            'vendor_allocation_ids': allocations,
            'currency_id': self.currency_id.id,
            'payment_date': self.payment_date or fields.Date.context_today(self),
            'group_payment': True,
            'installments_mode': 'full',
            'payment_difference_handling': 'open',
            'amount': amount,
            'custom_user_amount': amount,
            'custom_user_currency_id': self.currency_id.id,
        }
        values.update(journal_id=method.journal_id.id, payment_method_line_id=method.id)
        wizard = self.env['account.payment.register'].with_company(self.company_id).with_context(
            action['context'], active_model='account.move', active_ids=invoices.ids,
        ).create(values)
        return dict(action, res_id=wizard.id)

    def _check_payment_method(self):
        self.ensure_one()
        method = self.payment_method_line_id
        journal = method.journal_id
        if not method or method.payment_type != 'outbound' or journal.company_id != self.company_id or (
            journal.type not in ('bank', 'cash', 'credit')
            or (journal.currency_id and journal.currency_id != self.currency_id)
        ):
            raise UserError(_('Selecciona una forma de pago de proveedor compatible con la compañía y moneda del grupo.'))
        if not method.payment_account_id and not (
            journal.type == 'cash' and method.code == 'manual'
            and journal.default_account_id.active and journal.default_account_id.account_type == 'asset_cash'
        ):
            raise UserError(_('Configura la cuenta de pagos pendientes de la forma %(method)s del diario %(journal)s.',
                              method=method.name, journal=journal.display_name))
        return method

    def action_register_payment(self):
        return self.action_open_payment_wizard()

    def _mark_registered(self, payments):
        self.line_ids.filtered(lambda line: not line.selected).unlink()
        super().write({'state': 'registered', 'payment_ids': [Command.set(payments.ids)]})

    def action_prepare_followup_payment(self):
        self.ensure_one()
        if self.state != 'registered':
            raise UserError(_('Primero registra el pago de este grupo.'))
        invoices = self._selected_invoices().filtered_domain(self._invoice_domain())
        if not invoices:
            raise UserError(_('Las facturas de este grupo ya no tienen saldo disponible para pagar.'))
        draft = self.create({
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'payment_method_line_id': (self.payment_method_line_id or self._default_bank_payment_method()).id,
            'previous_group_id': self.id,
            'line_ids': [Command.create({
                'invoice_id': invoice.id,
                'amount_to_pay': invoice.amount_residual,
                'selected': True,
            }) for invoice in invoices],
        })
        return {
            'type': 'ir.actions.act_window', 'name': _('Pago complementario'),
            'res_model': self._name, 'view_mode': 'form', 'res_id': draft.id,
        }

    def action_view_invoices(self):
        self.ensure_one()
        return self._document_action('account.move', _('Facturas'), self._selected_invoices())

    def action_view_payments(self):
        self.ensure_one()
        return self._document_action('account.payment', _('Pagos'), self.payment_ids)

    def action_print_payment_group(self):
        return self._payment_group_report_action('pdf', 'new')

    def action_export_payment_group(self):
        return self._payment_group_report_action('xlsx', 'download')

    def _payment_group_report_action(self, extension, target):
        self.check_access('read')
        selection = 'all' if self.env.context.get('all_payment_groups') else ','.join(map(str, self.ids))
        if not selection:
            raise UserError(_('Selecciona al menos un folio de pagos agrupados.'))
        return {'type': 'ir.actions.act_url', 'target': target,
                'url': f'/entintados_pdv/vendor-payment-group/{selection}/{extension}'}

    def _payment_group_report_rows(self):
        self.check_access('read')
        states = dict(self._fields['state']._description_selection(self.env))
        for group in self:
            common = (group.name, group.company_id.display_name, states[group.state])
            yield (*common, _('Grupo'), group.name, '', group.payment_date, '', '',
                   group.currency_id.name, sum(group.payment_ids.mapped('amount')), None)
            for line in group.line_ids.filtered('selected'):
                yield (*common, _('Factura'), line.invoice_id.name, line.partner_id.display_name,
                       line.invoice_date, '', line.sat_payment_method_id.display_name or '',
                       line.currency_id.name, line.amount_to_pay, line.amount_residual)
            for payment in group.payment_ids:
                yield (*common, _('Pago'), payment.name, payment.partner_id.display_name, payment.date,
                       payment.journal_id.display_name, payment.l10n_mx_edi_payment_method_id.display_name or '',
                       payment.currency_id.name, payment.amount, None)

    def _payment_group_report_headers(self):
        return [_('Folio'), _('Compañía'), _('Estado'), _('Tipo'), _('Documento'), _('Proveedor'), _('Fecha'),
                _('Diario'), _('Forma de pago SAT'), _('Moneda'), _('Importe'), _('Saldo pendiente actual')]

    def _payment_group_xlsx(self):
        output = BytesIO()
        with xlsxwriter.Workbook(output, {
            'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False,
        }) as book:
            sheet = book.add_worksheet(_('Pagos agrupados'))
            heading = book.add_format({'bold': True, 'bg_color': '#E3EDF6'})
            money = book.add_format({'num_format': '#,##0.00'})
            date = book.add_format({'num_format': 'dd/mm/yyyy'})
            sheet.set_column('A:L', 20)
            sheet.set_column('B:B', 30)
            sheet.set_column('F:F', 30)
            sheet.set_column('I:I', 30)
            sheet.set_column('K:L', 22, money)
            sheet.write_row(0, 0, self._payment_group_report_headers(), heading)
            row = 0
            for row, values in enumerate(self._payment_group_report_rows(), 1):
                sheet.write_row(row, 0, values)
                if values[6]:
                    sheet.write_datetime(row, 6, values[6], date)
            sheet.freeze_panes(1, 0)
            sheet.autofilter(0, 0, row, 11)
        return output.getvalue()

    def _document_action(self, model, title, records):
        return {
            'type': 'ir.actions.act_window', 'name': title, 'res_model': model,
            'view_mode': 'list,form', 'domain': [('id', 'in', records.ids)],
            'context': {'create': False},
        }


class VendorPaymentGroupLine(models.Model):
    _name = 'vendor.payment.group.line'
    _description = 'Factura del pago agrupado'
    _check_company_auto = True

    group_id = fields.Many2one('vendor.payment.group', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='group_id.company_id', store=True)
    currency_id = fields.Many2one(related='group_id.currency_id')
    invoice_id = fields.Many2one('account.move', string='Factura', required=True, check_company=True, ondelete='restrict')
    partner_id = fields.Many2one(related='invoice_id.partner_id', string='Proveedor')
    selected = fields.Boolean(string='Pagar')
    invoice_date = fields.Date(related='invoice_id.invoice_date', string='Fecha de factura')
    due_date = fields.Date(related='invoice_id.invoice_date_due', string='Vencimiento')
    amount_residual = fields.Monetary(related='invoice_id.amount_residual', string='Saldo pendiente')
    amount_to_pay = fields.Monetary(string='Monto a pagar')
    payment_method_line_id = fields.Many2one('account.payment.method.line', string='Forma de pago', ondelete='restrict')
    sat_payment_method_id = fields.Many2one('l10n_mx_edi.payment.method', string='Forma de pago SAT')

    _unique_invoice = models.Constraint('UNIQUE(group_id, invoice_id)', 'La factura ya está en este pago agrupado.')

    def _amount_exceeds_balance(self):
        self.ensure_one()
        return bool(self.currency_id and self.invoice_id and
                    self.currency_id.compare_amounts(self.amount_to_pay, self.amount_residual) > 0)

    @api.constrains('amount_to_pay', 'invoice_id', 'selected')
    def _check_amount_within_balance(self):
        for line in self:
            if line.group_id.state == 'draft' and line._amount_exceeds_balance():
                raise UserError(_('El monto a pagar de %(invoice)s no puede superar su saldo pendiente.',
                                  invoice=line.invoice_id.display_name))

    @api.onchange('amount_to_pay')
    def _onchange_amount_to_pay(self):
        if self._amount_exceeds_balance():
            self.amount_to_pay = self.amount_residual

    @api.model_create_multi
    def create(self, vals_list):
        self.env['vendor.payment.group'].browse([vals['group_id'] for vals in vals_list])._lock_draft()
        return super().create(vals_list)

    def write(self, vals):
        if 'group_id' in vals:
            raise UserError(_('No se puede mover una factura a otro pago agrupado.'))
        self.group_id._lock_draft()
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_draft(self):
        self.group_id._lock_draft()
