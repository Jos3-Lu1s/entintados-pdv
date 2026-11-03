import math

from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError

class VendorCreditGroup(models.Model):
    _name = 'vendor.credit.group'
    _inherit = ['mail.thread', 'mail.activity.mixin']
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
            super(VendorCreditGroupLine, group.line_ids).write({'credit_id': False})
            super(VendorCreditGroup, group).write({'credit_ids': [Command.clear()], 'state': 'cancel'})

    def action_draft(self):
        for group in self:
            if group.state != 'cancel':
                raise UserError(_('Solo puedes restablecer a borrador una agrupación cancelada.'))
            if group.credit_ids:
                raise UserError(_('Cancela las notas relacionadas antes de restablecer la agrupación.'))
        super().write({'state': 'draft'})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('state', 'draft') != 'draft' or vals.get('credit_ids'):
                raise UserError(_('Las agrupaciones deben crearse en borrador y sin notas generadas.'))
            if vals.get('reason_id'):
                vals['reason'] = self.env['vendor.credit.reason'].browse(vals['reason_id']).name
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('vendor.credit.group') or _('New')
        return super().create(vals_list)

    def action_open_credit(self):
        self.ensure_one()
        return self.env['account.move']._action_open_moves(self.credit_ids, _('Notas de crédito'))

    def write(self, vals):
        if {'state', 'credit_ids', 'adjustment_revision'}.intersection(vals):
            raise UserError(_('Usa las acciones de la agrupación para cambiar su estado o sus notas.'))
        protected = {'partner_id', 'company_id', 'currency_id', 'journal_id', 'date', 'reason', 'reason_id', 'line_ids'}
        if protected.intersection(vals) and any(
                group.state != 'draft' or group.credit_ids for group in self):
            raise UserError(_('Solo puedes modificar una agrupación en borrador.'))
        if vals.get('reason_id'):
            vals = dict(vals, reason=self.env['vendor.credit.reason'].browse(vals['reason_id']).name)
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
    reason_id = fields.Many2one('vendor.credit.reason', string='Motivo', ondelete='restrict', check_company=True, tracking=True)
    reason = fields.Char(string='Descripción del motivo', required=True)
    adjustment_revision = fields.Integer(default=0, readonly=True, copy=False)
    line_ids = fields.One2many('vendor.credit.group.line', 'group_id', string='Facturas')
    amount_total = fields.Monetary(string='Total a acreditar', compute='_compute_amount_total', inverse='_inverse_amount_total')
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

    def _inverse_amount_total(self):
        for group in self:
            lines = group.line_ids.filtered('selected')
            total = group.amount_total
            capacity = sum(lines.mapped('invoice_total'))
            if (group.state != 'draft' or not lines or not math.isfinite(total)
                    or total <= 0 or group.currency_id.compare_amounts(total, capacity) > 0):
                raise UserError(_('Indica un total positivo dentro del importe de las facturas seleccionadas, en borrador.'))
            remaining = total
            for index, line in enumerate(lines):
                amount = (group.currency_id.round(total * line.invoice_total / capacity)
                          if index < len(lines) - 1 else group.currency_id.round(remaining))
                if amount <= 0:
                    raise UserError(_('El total es insuficiente para todas las facturas; reduce la selección.'))
                line.amount = amount
                remaining -= amount

    @api.onchange('reason_id')
    def _onchange_reason_id(self):
        if self.reason_id:
            self.reason = self.reason_id.name

    @api.model
    def _migrate_reason_catalog(self):
        """Preserve the free-text reasons of existing groups during upgrades."""
        highest_number = 0
        for group in self.search([]):
            if group.name.startswith('NCD-') and group.name[4:].isdigit():
                highest_number = max(highest_number, int(group.name[4:]))
            if not group.reason_id:
                name = group.reason or _('Otro')
                reasons = self.env['vendor.credit.reason'].with_context(active_test=False)
                reason = reasons.search([('name', '=', name), ('company_id', '=', group.company_id.id)], limit=1)
                if not reason:
                    reason = reasons.create({'name': name, 'company_id': group.company_id.id})
                super(VendorCreditGroup, group).write({'reason_id': reason.id})
            for line in group.line_ids.filtered(lambda item: not item.credit_id):
                credit = group.credit_ids.filtered(lambda move: line.invoice_id in move.vendor_credit_source_ids)
                if len(credit) == 1:
                    super(VendorCreditGroupLine, line).write({'credit_id': credit.id})
        # Older versions reset this sequence to 1 on each module update.
        sequence = self.env.ref('entintados_pdv.seq_vendor_credit_group')
        if (sequence.prefix == 'NCD-' and not sequence.suffix and not sequence.use_date_range
                and sequence.number_next_actual <= highest_number):
            sequence.number_next_actual = highest_number + sequence.number_increment

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
        self.check_access('write')
        with self.env.cr.savepoint():
            self.env['res.company']._with_locked_records(self)
            self.invalidate_recordset()
            return self._create_group_credits()

    def _create_group_credits(self):
        self.ensure_one()
        if self.credit_ids:
            return self.action_open_credit()
        if self.state == 'cancel':
            raise UserError(_('No puedes generar una nota desde una agrupación cancelada.'))
        if self.reason_id and not self.reason_id.active:
            raise UserError(_('Selecciona un motivo activo antes de generar las notas.'))
        self._validate_material_selection()
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
        self.env['res.company']._with_locked_records(invoices)
        invoices.invalidate_recordset()
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
            allocation._validate_credit_amount(allocation.amount)
            if self.currency_id.compare_amounts(invoice.amount_total, 0) <= 0:
                raise UserError(_('La factura debe tener un total positivo.'))
            payment_plan = allocation._payment_adjustment_plan(allocation.amount)
            if payment_plan:
                allocation._apply_payment_plan(payment_plan)
            commands = allocation._credit_line_commands(allocation.amount)
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
            allocation._set_credit_payable(credit)
            credit.action_post()
            self._reconcile_credit_with_invoices(credit, allocation)
            super(VendorCreditGroupLine, allocation).write({'credit_id': credit.id})
            credits |= credit
        self._generate_material_returns()
        (self.line_ids - selected_lines).unlink()
        super().write({'credit_ids': [Command.set(credits.ids)], 'state': 'generated'})
        return self.action_open_credit()

    def action_adjust_amounts(self):
        self.ensure_one()
        self.check_access('write')
        if self.state != 'generated':
            raise UserError(_('Solo puedes ajustar una agrupación generada.'))
        return {
            'type': 'ir.actions.act_window', 'name': _('Ajustar notas y pagos'),
            'res_model': 'vendor.credit.group.adjustment', 'view_mode': 'form', 'target': 'new',
            'context': {'default_group_id': self.id},
        }

    def _apply_amount_adjustment(self, amounts, reason, explanation, revision):
        self.ensure_one()
        self.check_access('write')
        with self.env.cr.savepoint():
            self.env['res.company']._with_locked_records(self)
            self.invalidate_recordset()
            if self.state != 'generated' or self.adjustment_revision != revision:
                raise UserError(_('La agrupación cambió. Cierra el asistente y vuelve a abrirlo.'))
            if set(amounts) != set(self.line_ids.ids) or not explanation.strip():
                raise UserError(_('Revisa las facturas y explica el ajuste.'))
            reason.check_access('read')
            if (reason.operation_type == 'return') != bool(self.return_picking_ids):
                raise UserError(_('No puedes cambiar entre ajuste monetario y devolución después de generar las notas. Crea otra agrupación.'))
            if not reason.active or (reason.company_id and reason.company_id != self.company_id):
                raise UserError(_('Selecciona un motivo activo de la misma compañía.'))
            if reason != self.reason_id and any(
                    credit.state != 'posted' or getattr(credit, 'l10n_mx_edi_cfdi_uuid', False)
                    or credit.l10n_mx_edi_cfdi_state in ('sent', 'cancel_requested', 'cancel')
                    for credit in self.credit_ids):
                raise UserError(_('No se puede cambiar el motivo de una agrupación con notas fiscales o no contabilizadas.'))
            plans, changes = [], []
            for allocation in self.line_ids:
                credit = allocation._group_credit()
                allocation._validate_credit_amount(amounts[allocation.id])
                if self.currency_id.is_zero(amounts[allocation.id] - allocation.amount) and reason == self.reason_id:
                    continue
                if (allocation.invoice_id.state != 'posted'
                        or credit.vendor_credit_source_ids != allocation.invoice_id
                        or self.currency_id.compare_amounts(credit.amount_total, allocation.amount)):
                    raise UserError(_('La factura o su nota cambió fuera de la agrupación. Revisa sus documentos antes de ajustar.'))
                if (credit.state != 'posted' or getattr(credit, 'l10n_mx_edi_cfdi_uuid', False)
                        or credit.l10n_mx_edi_cfdi_state in ('sent', 'cancel_requested', 'cancel')):
                    raise UserError(_('La nota %s tiene restricciones fiscales o no está contabilizada.') % credit.display_name)
                # The credit must still be applied exclusively to its original bill.
                terms = credit.line_ids.filtered(lambda line: line.account_type == 'liability_payable')
                counterparts = (terms.matched_debit_ids.debit_move_id.move_id
                                | terms.matched_credit_ids.credit_move_id.move_id)
                if counterparts - allocation.invoice_id or not credit.currency_id.is_zero(credit.amount_residual):
                    raise UserError(_('La nota %s ya no está aplicada únicamente a su factura de origen.') % credit.display_name)
                plan = allocation._payment_adjustment_plan(amounts[allocation.id], credit)
                plans.append((allocation, credit, amounts[allocation.id], plan))
            if not plans:
                raise UserError(_('No hay importes distintos para ajustar.'))
            documents = self.line_ids.invoice_id | self.credit_ids
            payments = self.env['account.payment']
            for allocation, credit, amount, plan in plans:
                if plan:
                    documents |= plan[0].move_id
                    payments |= plan[0]
            self.env['res.company']._with_locked_records(documents)
            self.env['res.company']._with_locked_records(documents.line_ids)
            if payments:
                self.env['res.company']._with_locked_records(payments)
                payments.invalidate_recordset()
            plans = [(line, credit, amount, line._payment_adjustment_plan(amount, credit))
                     for line, credit, amount, plan in plans]
            for allocation, credit, amount, plan in plans:
                old = allocation.amount
                credit.line_ids.filtered(lambda line: line.account_type == 'liability_payable').remove_move_reconcile()
                credit.button_draft()
                if plan:
                    allocation._apply_payment_plan(plan)
                credit.write({'invoice_line_ids': [Command.clear()] + allocation._credit_line_commands(amount, reason),
                              'ref': reason.name})
                if self.currency_id.compare_amounts(credit.amount_total, amount):
                    raise UserError(_('Los impuestos o redondeos no permiten obtener el importe solicitado.'))
                allocation._set_credit_payable(credit)
                credit.action_post()
                self._reconcile_credit_with_invoices(credit, allocation)
                if not self.currency_id.is_zero(allocation.invoice_id.amount_residual):
                    raise UserError(_('No se pudo liquidar la factura %(invoice)s; quedó un saldo de %(residual)s. El ajuste completo fue revertido.',
                                      invoice=allocation.invoice_id.name, residual=allocation.invoice_id.amount_residual))
                super(VendorCreditGroupLine, allocation).write({'amount': amount, 'credit_id': credit.id})
                changes.append('%s: %s → %s' % (allocation.invoice_id.name, old, amount))
            self.credit_ids.write({'ref': reason.name})
            super().write({'reason_id': reason.id, 'reason': reason.name,
                           'adjustment_revision': self.adjustment_revision + 1})
            self.message_post(body=_('Ajuste de notas y pagos. %(details)s. Justificación: %(explanation)s',
                                     details='; '.join(changes), explanation=explanation))
            return self.action_open_credit()



class VendorCreditGroupLine(models.Model):
    _name = 'vendor.credit.group.line'
    _description = 'Importe de crédito por factura'
    _rec_name = 'invoice_id'

    group_id = fields.Many2one('vendor.credit.group', required=True, ondelete='cascade')
    selected = fields.Boolean(string='Incluir', default=True)
    invoice_id = fields.Many2one('account.move', string='Factura', required=True)
    currency_id = fields.Many2one(related='group_id.currency_id')
    residual = fields.Monetary(related='invoice_id.amount_residual', string='Saldo pendiente', readonly=True)
    amount = fields.Monetary(string='Importe a acreditar (con impuestos)', required=True)
    credit_id = fields.Many2one('account.move', readonly=True, copy=False, ondelete='restrict')
    payment_id = fields.Many2one('account.payment', readonly=True, copy=False, ondelete='restrict')
    payment_to_adjust_id = fields.Many2one('account.payment', string='Pago a ajustar', compute='_compute_proposed_payment')
    proposed_payment_amount = fields.Monetary(string='Nuevo importe del pago', compute='_compute_proposed_payment')
    invoice_total = fields.Monetary(related='invoice_id.amount_total', string='Importe total de la factura', readonly=True)

    def _group_credit(self):
        self.ensure_one()
        credit = self.credit_id or self.group_id.credit_ids.filtered(
            lambda move: self.invoice_id in move.vendor_credit_source_ids)
        if len(credit) != 1:
            raise UserError(_('No se identificó una nota única para la factura %s.') % self.invoice_id.display_name)
        return credit

    def _candidate_payment(self):
        self.ensure_one()
        return (self.payment_id or self.invoice_id.reconciled_payment_ids.filtered(
            lambda payment: payment.payment_type == 'outbound' and payment.partner_type == 'supplier'
            and payment.move_id.state == 'posted').sorted('id', reverse=True)[:1])

    @api.depends('amount', 'invoice_id.amount_residual', 'invoice_id.reconciled_payment_ids.amount',
                 'payment_id.amount', 'credit_id.amount_total')
    def _compute_proposed_payment(self):
        for line in self:
            payment = line._candidate_payment()
            line.payment_to_adjust_id = payment
            linked = line.credit_id or line.group_id.credit_ids.filtered(
                lambda move: line.invoice_id in move.vendor_credit_source_ids)[:1]
            credit = linked.amount_total if linked else 0
            line.proposed_payment_amount = (payment.amount + line.invoice_id.amount_residual + credit - line.amount
                                            if payment else 0)

    def _validate_credit_amount(self, amount):
        if (not math.isfinite(amount) or amount <= 0
                or self.currency_id.compare_amounts(amount, self.invoice_total) > 0):
            raise UserError(_('El importe de la nota debe ser positivo y no superar el total de su factura.'))

    def _set_credit_payable(self, credit):
        payable = self.invoice_id.line_ids.filtered(lambda line: line.account_type == 'liability_payable').account_id
        if len(payable) != 1:
            raise UserError(_('La factura debe tener una sola cuenta por pagar para liquidarla.'))
        credit.line_ids.filtered(lambda line: line.display_type == 'payment_term').account_id = payable

    def _credit_line_commands(self, amount, reason=None):
        reason = reason or self.group_id.reason_id
        reason._check_accounts()
        payable = self.invoice_id.line_ids.filtered(lambda item: item.account_type == 'liability_payable').account_id
        if reason.payable_account_id and payable != reason.payable_account_id:
            raise UserError(_('La cuenta por pagar del motivo debe coincidir con la factura %s.') % self.invoice_id.display_name)
        material = self.group_id.material_line_ids.filtered(lambda item: item.allocation_id == self)
        if reason.operation_type == 'return':
            base = sum(item.invoice_line_id.price_total * item.quantity / item.invoice_line_id.quantity for item in material)
            source = [(item.invoice_line_id, item.quantity) for item in material]
        else:
            base = self.invoice_total
            source = [(line, line.quantity) for line in self.invoice_id.invoice_line_ids.filtered(lambda item: item.display_type == 'product')]
        if base <= 0:
            raise UserError(_('Selecciona material con importe positivo para la factura %s.') % self.invoice_id.display_name)
        commands = []
        for line, quantity in source:
            values = line.copy_data({'name': '%s: %s' % (self.invoice_id.name, line.name or ''),
                                    'quantity': quantity,
                                    'price_unit': line.price_unit * amount / base})[0]
            for key in ('move_id', 'purchase_line_id', 'sale_line_ids'):
                values.pop(key, None)
            if reason.operation_type != 'return':
                values.update(product_id=False, product_uom_id=False, quantity=1,
                              price_unit=line.price_unit * quantity * amount / base)
            if reason.account_id:
                values['account_id'] = reason.account_id.id
            commands.append(Command.create(values))
        return commands

    def _payment_adjustment_plan(self, amount, credit=None):
        self.ensure_one()
        invoice = self.invoice_id
        delta = self.currency_id.round(invoice.amount_residual + (credit.amount_total if credit else 0) - amount)
        if self.currency_id.is_zero(delta):
            return None
        payment = self._candidate_payment()
        if not payment:
            raise UserError(_('La factura %s necesita un pago registrado para recalcular su importe.') % invoice.display_name)
        payment.check_access('write')
        liquidity = payment.move_id.line_ids.filtered(lambda line: line.account_type != 'liability_payable')
        bank_matches = liquidity.matched_debit_ids | liquidity.matched_credit_ids
        fiscal_history = self.env['l10n_mx_edi.document'].search_count([
            ('move_id', '=', payment.move_id.id), ('state', 'in', ['payment_sent', 'payment_cancel'])])
        if (payment.move_id.state != 'posted' or payment.is_matched or payment.is_sent
                or bank_matches or fiscal_history
                or payment.payment_method_line_id.code != 'manual'
                or payment.currency_id != self.currency_id or payment.company_id != self.group_id.company_id
                or payment.partner_id.commercial_partner_id != invoice.partner_id.commercial_partner_id
                or getattr(payment.move_id, 'l10n_mx_edi_cfdi_uuid', False)):
            raise UserError(_('El pago %s debe ser manual, sin conciliación bancaria ni timbrado, y de la misma compañía y moneda.') % payment.display_name)
        terms = payment.move_id.line_ids.filtered(lambda line: line.account_type == 'liability_payable')
        partials = terms.matched_debit_ids | terms.matched_credit_ids
        counterpart_moves = (partials.debit_move_id.move_id | partials.credit_move_id.move_id) - payment.move_id
        if (counterpart_moves != invoice or not terms
                or any(not line.currency_id.is_zero(line.amount_residual_currency) for line in terms)):
            raise UserError(_('El pago %s debe estar aplicado únicamente a esta factura y no tener saldo sin aplicar. Separa los pagos compartidos antes de ajustar.') % payment.display_name)
        target = self.currency_id.round(payment.amount + delta)
        if target <= 0:
            raise UserError(_('El ajuste dejaría el pago en cero o negativo. Cancela o separa ese pago antes de continuar.'))
        return payment, target

    def _apply_payment_plan(self, plan):
        payment, target = plan
        old = payment.amount
        self.env['res.company']._with_locked_records(payment)
        self.env['res.company']._with_locked_records(payment.move_id | self.invoice_id)
        payment.move_id.line_ids.filtered(lambda line: line.account_type == 'liability_payable').remove_move_reconcile()
        payment.action_draft()
        payment.amount = target
        payment.action_post()
        terms = (payment.move_id | self.invoice_id).line_ids.filtered(
            lambda line: line.account_type == 'liability_payable' and not line.reconciled)
        for account in terms.account_id:
            terms.filtered(lambda line: line.account_id == account).reconcile()
        super(VendorCreditGroupLine, self).write({'payment_id': payment.id})
        payment.message_post(body=_('Pago ajustado por la agrupación %(group)s: %(old)s → %(new)s.',
                                    group=self.group_id.name, old=old, new=target))

    def _check_editable_group(self):
        if any(group.state != 'draft' or group.credit_ids for group in self.group_id):
            raise UserError(_('Solo puedes modificar las líneas de una agrupación en borrador.'))

    @api.model_create_multi
    def create(self, vals_list):
        if any(values.get('credit_id') or values.get('payment_id') for values in vals_list):
            raise UserError(_('Las referencias contables se asignan al generar o ajustar las notas.'))
        groups = self.env['vendor.credit.group'].browse([vals['group_id'] for vals in vals_list if vals.get('group_id')])
        if any(group.state != 'draft' or group.credit_ids for group in groups):
            raise UserError(_('Solo puedes agregar líneas a una agrupación en borrador.'))
        return super().create(vals_list)

    def write(self, vals):
        if {'credit_id', 'payment_id'}.intersection(vals):
            raise UserError(_('Las referencias contables se asignan al generar o ajustar las notas.'))
        self._check_editable_group()
        if vals.get('group_id'):
            group = self.env['vendor.credit.group'].browse(vals['group_id'])
            if group.state != 'draft' or group.credit_ids:
                raise UserError(_('Solo puedes mover líneas a una agrupación en borrador.'))
        return super().write(vals)

    def unlink(self):
        self._check_editable_group()
        return super().unlink()
