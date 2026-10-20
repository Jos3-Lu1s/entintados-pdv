from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError

class VendorCreditGroup(models.Model):
    _name = 'vendor.credit.group'
    _description = 'Nota de crédito agrupada de proveedor'
    _check_company_auto = True
    _rec_name = 'name'
    _order = 'date desc, id desc'

    name = fields.Char(
        string="Folio",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
    )

    credit_ids = fields.Many2many(
        'account.move', 'vendor_credit_group_move_rel', 'group_id', 'move_id',
        string='Notas de crédito', readonly=True, copy=False, check_company=True,
    )
    credit_count = fields.Integer(compute='_compute_credit_count')

    @api.depends('credit_ids')
    def _compute_credit_count(self):
        for group in self:
            group.credit_count = len(group.credit_ids)

    state = fields.Selection([('draft', 'Borrador'), ('generated', 'Nota generada'), ('cancel', 'Cancelado')], default='draft', required=True, copy=False)
    invoice_count = fields.Integer(compute='_compute_invoice_count')

    @api.depends('line_ids.selected')
    def _compute_invoice_count(self):
        for group in self:
            group.invoice_count = len(group.line_ids.filtered('selected'))

    def action_view_invoices(self):
        self.ensure_one()
        invoices = self.line_ids.filtered('selected').invoice_id
        return self.env['account.move']._action_open_moves(invoices, _('Facturas'))

    def action_cancel(self):
        for group in self:
            if group.state == 'cancel':
                continue
            for credit in group.credit_ids:
                if credit.state == 'posted':
                    credit.button_draft()
                if credit.state == 'draft':
                    credit.button_cancel()
                credit.vendor_credit_source_ids = [Command.clear()]
            group.write({'credit_ids': [Command.clear()], 'state': 'cancel'})

    def action_draft(self):
        for group in self:
            if group.state != 'cancel':
                raise UserError(_('Solo puedes restablecer a borrador una agrupación cancelada.'))
            if group.credit_ids:
                raise UserError(_('Cancela las notas relacionadas antes de restablecer la agrupación.'))
        self.write({'state': 'draft'})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('vendor.credit.group') or _('New')
        return super().create(vals_list)

    def action_open_credit(self):
        self.ensure_one()
        return self.env['account.move']._action_open_moves(self.credit_ids, _('Notas de crédito'))

    def write(self, vals):
        protected = {'partner_id', 'company_id', 'currency_id', 'journal_id', 'date', 'reason', 'line_ids'}
        if protected.intersection(vals) and any(
                group.state != 'draft' or group.credit_ids for group in self):
            raise UserError(_('Solo puedes modificar una agrupación en borrador.'))
        return super().write(vals)

    def unlink(self):
        if any(group.credit_ids for group in self):
            raise UserError(_('No puedes eliminar una agrupación que ya generó su nota.'))
        return super().unlink()
    
    def _reconcile_credit_with_invoices(self, credit, allocations):
        self.ensure_one()
        invoices = allocations.invoice_id
        lines = (credit | invoices).line_ids.filtered(
            lambda line: line.account_id.account_type == 'liability_payable'
            and not line.reconciled
        )
        # Conciliar por cuenta, incluyendo los vencimientos múltiples.
        for account in lines.account_id:
            account_lines = lines.filtered(lambda line: line.account_id == account)
            if (account_lines.filtered(lambda line: line.move_id == credit)
                    and account_lines.filtered(lambda line: line.move_id in invoices)):
                account_lines.reconcile()

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    partner_id = fields.Many2one('res.partner', string='Proveedor', required=True, check_company=True)
    currency_id = fields.Many2one('res.currency', string='Moneda', required=True, default=lambda self: self.env.company.currency_id)
    journal_id = fields.Many2one(
        'account.journal', string='Diario de compras', required=True, check_company=True,
        compute='_compute_journal_id', store=True, readonly=False, precompute=True,
        domain="[('type', '=', 'purchase'), ('company_id', '=', company_id)]",
    )
    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today)
    reason = fields.Char(string='Motivo', required=True)
    line_ids = fields.One2many('vendor.credit.group.line', 'group_id', string='Facturas')
    amount_total = fields.Monetary(string='Total a acreditar', compute='_compute_amount_total')
    selection_toggle_label = fields.Char(compute='_compute_selection_toggle_label')

    @api.depends('company_id', 'currency_id')
    def _compute_journal_id(self):
        Journal = self.env['account.journal']
        for group in self:
            journal = group.journal_id
            if (journal and journal.type == 'purchase' and journal.company_id == group.company_id
                    and (not journal.currency_id or journal.currency_id == group.currency_id)):
                continue
            domain = [('type', '=', 'purchase'), ('company_id', '=', group.company_id.id)]
            preferred = Journal.search(domain + [('currency_id', '=', group.currency_id.id)], limit=1)
            group.journal_id = preferred or Journal.search(domain + [('currency_id', '=', False)], limit=1)

    @api.depends('line_ids', 'line_ids.selected')
    def _compute_selection_toggle_label(self):
        for group in self:
            all_selected = bool(group.line_ids) and all(group.line_ids.mapped('selected'))
            group.selection_toggle_label = _('Deseleccionar todas') if all_selected else _('Seleccionar todas')

    def action_toggle_invoice_selection(self):
        self.ensure_one()
        if self.state != 'draft' or self.credit_ids:
            raise UserError(_('Solo puedes cambiar la selección de una agrupación en borrador.'))
        if self.line_ids:
            self.line_ids.write({'selected': not all(self.line_ids.mapped('selected'))})
        return True

    @api.depends('line_ids.amount', 'line_ids.selected')
    def _compute_amount_total(self):
        for wizard in self:
            wizard.amount_total = sum(wizard.line_ids.filtered('selected').mapped('amount'))

    @api.onchange('partner_id', 'company_id', 'currency_id')
    def _onchange_scope(self):
        self.line_ids = [Command.clear()]
        if self.partner_id and self.partner_id.distributed_notes:
            self._populate_lines_from_partner()

    def _populate_lines_from_partner(self):
        """Carga automáticamente las facturas con pago parcial del proveedor,
        solo cuando el proveedor tiene activo 'distributed_notes'."""
        self.ensure_one()
        domain = [
            ('move_type', '=', 'in_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', '=', 'partial'),
            ('partner_id', '=', self.partner_id.id),
            ('company_id', '=', self.company_id.id),
            ('currency_id', '=', self.currency_id.id),
        ]
        invoices = self.env['account.move'].search(domain)
        self.line_ids = [
            Command.create({
                'invoice_id': invoice.id,
                'amount': invoice.amount_residual,
                'selected': True,
            })
            for invoice in invoices
        ]

    def action_create_credit(self):
        self.ensure_one()
        if self.credit_ids:
            return self.action_open_credit()
        if self.state == 'cancel':
            raise UserError(_('No puedes generar una nota desde una agrupación cancelada.'))
        selected_lines = self.line_ids.filtered('selected')
        if not selected_lines:
            raise UserError(_('Selecciona al menos una factura.'))
        if self.company_id not in self.env.companies:
            raise UserError(_('La compañía no está habilitada en la sesión.'))
        if self.journal_id.type != 'purchase' or self.journal_id.company_id != self.company_id:
            raise UserError(_('Selecciona un diario de compras de la misma compañía.'))
        if self.journal_id.currency_id and self.journal_id.currency_id != self.currency_id:
            raise UserError(_('La moneda del diario debe coincidir con la nota.'))
        invoices = selected_lines.mapped('invoice_id')
        if len(invoices) != len(selected_lines):
            raise UserError(_('No puedes repetir una factura.'))
        credits = self.env['account.move']
        for allocation in selected_lines:
            invoice = allocation.invoice_id
            if (invoice.move_type != 'in_invoice' or invoice.state != 'posted'
                    or invoice.payment_state != 'partial'
                    or invoice.partner_id != self.partner_id
                    or invoice.company_id != self.company_id
                    or invoice.currency_id != self.currency_id):
                raise UserError(_('Todas las facturas deben estar contabilizadas, tener pago parcial y pertenecer al mismo proveedor, compañía y moneda.'))
            if (self.currency_id.compare_amounts(allocation.amount, 0) <= 0
                    or self.currency_id.compare_amounts(allocation.amount, invoice.amount_residual) > 0):
                raise UserError(_('El importe de %s debe ser mayor que cero y no superar su saldo pendiente.') % invoice.display_name)
            if self.currency_id.compare_amounts(invoice.amount_total, 0) <= 0:
                raise UserError(_('La factura debe tener un total positivo.'))
            commands = []
            ratio = allocation.amount / invoice.amount_total
            for line in invoice.invoice_line_ids.filtered(lambda item: item.display_type == 'product'):
                values = line.copy_data({
                    'name': '%s: %s' % (invoice.name, line.name or ''),
                    'price_unit': line.price_unit * ratio,
                })[0]
                for key in ('move_id', 'purchase_line_id', 'sale_line_ids'):
                    values.pop(key, None)
                commands.append(Command.create(values))
            credit = self.env['account.move'].with_company(self.company_id).with_context(
                default_move_type='in_refund',
            ).create({
                'move_type': 'in_refund', 'partner_id': self.partner_id.id,
                'company_id': self.company_id.id, 'currency_id': self.currency_id.id,
                'journal_id': self.journal_id.id, 'invoice_date': self.date, 'date': self.date,
                'ref': self.reason, 'invoice_origin': invoice.name,
                'vendor_credit_source_ids': [Command.set(invoice.ids)],
                'invoice_line_ids': commands,
            })
            if self.currency_id.compare_amounts(credit.amount_total, allocation.amount):
                raise UserError(_(
                    'Los impuestos o redondeos de %s no permiten obtener exactamente '
                    'el importe solicitado. Ajusta el importe.'
                ) % invoice.display_name)
            credit.action_post()
            self._reconcile_credit_with_invoices(credit, allocation)
            credits |= credit
        (self.line_ids - selected_lines).unlink()
        self.write({'credit_ids': [Command.set(credits.ids)], 'state': 'generated'})
        return self.action_open_credit()



class VendorCreditGroupLine(models.Model):
    _name = 'vendor.credit.group.line'
    _description = 'Importe de crédito por factura'

    group_id = fields.Many2one('vendor.credit.group', required=True, ondelete='cascade')
    selected = fields.Boolean(string='Incluir', default=True)
    invoice_id = fields.Many2one('account.move', string='Factura', required=True)
    currency_id = fields.Many2one(related='group_id.currency_id')
    residual = fields.Monetary(related='invoice_id.amount_residual', string='Saldo pendiente', readonly=True)
    amount = fields.Monetary(string='Importe a acreditar (con impuestos)', required=True)

    def _check_editable_group(self):
        if any(group.state != 'draft' or group.credit_ids for group in self.group_id):
            raise UserError(_('Solo puedes modificar las líneas de una agrupación en borrador.'))

    @api.model_create_multi
    def create(self, vals_list):
        groups = self.env['vendor.credit.group'].browse([vals['group_id'] for vals in vals_list if vals.get('group_id')])
        if any(group.state != 'draft' or group.credit_ids for group in groups):
            raise UserError(_('Solo puedes agregar líneas a una agrupación en borrador.'))
        return super().create(vals_list)

    def write(self, vals):
        self._check_editable_group()
        if vals.get('group_id'):
            group = self.env['vendor.credit.group'].browse(vals['group_id'])
            if group.state != 'draft' or group.credit_ids:
                raise UserError(_('Solo puedes mover líneas a una agrupación en borrador.'))
        return super().write(vals)

    def unlink(self):
        self._check_editable_group()
        return super().unlink()
