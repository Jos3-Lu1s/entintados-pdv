from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    vendor_credit_source_ids = fields.Many2many(
        'account.move', 'vendor_credit_source_rel', 'credit_id', 'invoice_id',
        string='Facturas de origen', copy=False, check_company=True,
    )

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

    credit_id = fields.Many2one('account.move', string='Nota de crédito', readonly=True, copy=False, check_company=True)
    state = fields.Selection([('draft', 'Borrador'), ('generated', 'Nota generada')], compute='_compute_state', store=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('vendor.credit.group') or _('New')
        return super().create(vals_list)

    @api.depends('credit_id')
    def _compute_state(self):
        for record in self:
            record.state = 'generated' if record.credit_id else 'draft'

    def action_open_credit(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'view_mode': 'form', 'res_id': self.credit_id.id, 'target': 'current'}

    def write(self, vals):
        protected = {'partner_id', 'company_id', 'currency_id', 'journal_id', 'date', 'reason', 'line_ids'}
        if protected.intersection(vals) and any(self.mapped('credit_id')):
            raise UserError(_('No puedes modificar una agrupación que ya generó su nota.'))
        return super().write(vals)

    def unlink(self):
        if any(self.mapped('credit_id')):
            raise UserError(_('No puedes eliminar una agrupación que ya generó su nota.'))
        return super().unlink()

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    partner_id = fields.Many2one('res.partner', string='Proveedor', required=True, check_company=True)
    currency_id = fields.Many2one('res.currency', string='Moneda', required=True, default=lambda self: self.env.company.currency_id)
    journal_id = fields.Many2one('account.journal', string='Diario de compras', required=True, check_company=True)
    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today)
    reason = fields.Char(string='Motivo', required=True)
    line_ids = fields.One2many('vendor.credit.group.line', 'group_id', string='Facturas')
    amount_total = fields.Monetary(string='Total a acreditar', compute='_compute_amount_total')

    @api.depends('line_ids.amount', 'line_ids.selected')
    def _compute_amount_total(self):
        for wizard in self:
            wizard.amount_total = sum(wizard.line_ids.filtered('selected').mapped('amount'))

    @api.onchange('partner_id', 'company_id', 'currency_id')
    def _onchange_scope(self):
        self.line_ids = [Command.clear()]
        if self.journal_id.company_id != self.company_id:
            self.journal_id = False
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
        if self.credit_id:
            return self.action_open_credit()
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
        commands = []
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
            'ref': self.reason, 'invoice_origin': ', '.join(invoices.mapped('name')),
            'vendor_credit_source_ids': [Command.set(invoices.ids)],
            'invoice_line_ids': commands,
        })
        if self.currency_id.compare_amounts(credit.amount_total, self.amount_total):
            raise UserError(_('Los impuestos o redondeos de estas facturas no permiten obtener exactamente el importe solicitado. Ajusta los importes o genera notas individuales.'))
        self.credit_id = credit
        return {
            'type': 'ir.actions.act_window', 'name': _('Nota de crédito'),
            'res_model': 'account.move', 'view_mode': 'form', 'res_id': credit.id,
            'target': 'current', 'context': {'default_move_type': 'in_refund'},
        }


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
        if any(self.mapped('group_id.credit_id')):
            raise UserError(_('No puedes modificar las líneas de una nota generada.'))

    @api.model_create_multi
    def create(self, vals_list):
        groups = self.env['vendor.credit.group'].browse([vals['group_id'] for vals in vals_list if vals.get('group_id')])
        if any(groups.mapped('credit_id')):
            raise UserError(_('No puedes agregar líneas a una nota generada.'))
        return super().create(vals_list)

    def write(self, vals):
        self._check_editable_group()
        if vals.get('group_id') and self.env['vendor.credit.group'].browse(vals['group_id']).credit_id:
            raise UserError(_('No puedes mover líneas a una nota generada.'))
        return super().write(vals)

    def unlink(self):
        self._check_editable_group()
        return super().unlink()