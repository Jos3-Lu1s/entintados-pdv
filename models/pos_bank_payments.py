from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    pos_card_default_journal_id = fields.Many2one('account.journal', string='Diario predeterminado para tarjetas',
        check_company=True, domain="[('type', '=', 'bank'), ('company_id', '=', id)]")
    def _payment_method_defaults(self):
        self.ensure_one()
        journal = self.pos_card_default_journal_id
        if not journal:
            journals = self.env['account.journal'].search([
                ('company_id', '=', self.id), ('type', '=', 'bank')], limit=2)
            if len(journals) == 1:
                journal = journals
        if journal and (journal.type != 'bank' or journal.company_id != self or not journal.active):
            raise UserError(_('Selecciona un diario bancario activo de la misma compañía.'))
        account = journal.inbound_payment_method_line_ids.filtered(lambda line: line.code == 'manual').payment_account_id[:1]
        if account and (not account.active or not account.reconcile or self not in account.company_ids
                        or account.account_type not in ('asset_current', 'asset_cash')):
            account = self.env['account.account']
        if not account and journal:
            account = self.env['pos.payment.method'].search([
                ('company_id', '=', self.id), ('journal_id', '=', journal.id),
                ('outstanding_account_id.active', '=', True), ('outstanding_account_id.reconcile', '=', True),
                ('outstanding_account_id.company_ids', 'in', self.ids),
                ('outstanding_account_id.account_type', 'in', ['asset_current', 'asset_cash']),
            ], limit=1).outstanding_account_id
        return {'journal_id': journal.id, 'outstanding_account_id': account.id}


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_card_default_journal_id = fields.Many2one(
        related='company_id.pos_card_default_journal_id', readonly=False)


class PosCardBank(models.Model):
    _name = 'pos.card.bank'
    _inherit = ['pos.load.mixin']
    _description = 'Banco para cobros con tarjeta'
    _order = 'name, id'
    _check_company_auto = True

    name = fields.Char(string='Banco / terminal', required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)
    journal_id = fields.Many2one('account.journal', string='Diario específico', check_company=True,
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]",
        help='Si queda vacío, se usa el diario predeterminado para tarjetas de la compañía.')

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('company_id', '=', config.company_id.id), ('active', '=', True)]

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name', 'active']

    @api.constrains('journal_id', 'company_id')
    def _check_card_journal(self):
        for bank in self:
            if bank.journal_id and (bank.journal_id.type != 'bank' or bank.journal_id.company_id != bank.company_id):
                raise ValidationError(_('Selecciona un diario bancario de la misma compañía.'))


class PosBankPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    collection_type = fields.Selection([
        ('standard', 'Otro'), ('transfer', 'Transferencia'),
        ('check', 'Cheque'), ('card', 'Tarjeta / terminal'),
    ], string='Tipo de cobro', default='standard', required=True)
    terminal_label = fields.Char(string='Banco / terminal',
                                 help='Nombre visible en caja, por ejemplo BBVA o Santander.')
    card_bank_id = fields.Many2one('pos.card.bank', string='Banco de tarjeta', check_company=True,
        ondelete='restrict', domain="[('company_id', '=', company_id)]")
    card_bank_name = fields.Char(string='Nombre de banco anterior', related='card_bank_id.name')
    card_payment_code = fields.Char(related='l10n_mx_edi_payment_method_id.code')

    @api.model
    def _ensure_transfer_check_methods(self):
        """Add the two native methods on upgrade, preserving existing bank settings."""
        for company in self.env['pos.config'].search([]).company_id:
            configs = self.env['pos.config'].search([('company_id', '=', company.id)])
            methods = self.with_company(company)
            bank_methods = methods.search([('company_id', '=', company.id), ('journal_id.type', '=', 'bank')])
            # A branch's existing methods may point at the parent's journal, which the bank constraint rejects.
            journal = bank_methods.journal_id.filtered(lambda item: item.company_id == company)[:1] or self.env['account.journal'].search([
                ('company_id', '=', company.id), ('type', '=', 'bank')], limit=1)
            if not journal:
                code, suffix = 'BNK', 0
                while self.env['account.journal'].search_count([('company_id', '=', company.id), ('code', '=', code)]):
                    suffix += 1
                    code = 'BNK%s' % suffix
                journal = self.env['account.journal'].with_company(company).create({
                    'name': 'Banco', 'code': code,
                    'type': 'bank', 'company_id': company.id})
            account = bank_methods.filtered(lambda item: item.outstanding_account_id.active
                and item.outstanding_account_id.reconcile
                and company in item.outstanding_account_id.company_ids
                and item.outstanding_account_id.account_type in ('asset_current', 'asset_cash'))[:1].outstanding_account_id
            prepared = methods.browse()
            for kind, name, sat in (('transfer', 'Transferencia', 'transferencia'), ('check', 'Cheque', 'cheque')):
                method = methods.with_context(active_test=False).search([
                    ('company_id', '=', company.id), ('collection_type', '=', kind)], limit=1)
                if not method:
                    account = account or configs[:1]._bank_pending_account('Banco', 0)
                    method = methods.create({'name': name, 'company_id': company.id,
                        'collection_type': kind, 'journal_id': journal.id, 'outstanding_account_id': account.id,
                        'payment_method_type': 'none',
                        'l10n_mx_edi_payment_method_id': self.env.ref('l10n_mx_edi.payment_method_' + sat).id})
                if method.active:
                    prepared |= method
            for config in configs:
                missing = prepared - config.payment_method_ids
                if missing:
                    config.with_context(bypass_payment_method_ids_forbidden_change=True).write({
                        'payment_method_ids': [Command.link(method.id) for method in missing]})

    @api.model
    def _load_pos_data_fields(self, config):
        names = super()._load_pos_data_fields(config)
        return names + ['collection_type', 'terminal_label', 'card_bank_name', 'card_payment_code'] if names else names

    @api.onchange('collection_type')
    def _onchange_collection_type(self):
        mapping = {'transfer': 'transferencia', 'check': 'cheque', 'card': 'tarjeta_debito'}
        if self.collection_type in mapping:
            self.l10n_mx_edi_payment_method_id = self.env.ref(
                'l10n_mx_edi.payment_method_' + mapping[self.collection_type])
        if self.collection_type != 'card':
            self.card_bank_id = False
        elif not self._origin.id and not self.journal_id and self.company_id:
            self.update(self.company_id._payment_method_defaults())

    @api.constrains('collection_type', 'journal_id', 'outstanding_account_id', 'company_id', 'terminal_label', 'card_bank_id')
    def _check_bank_collection_configuration(self):
        for method in self.filtered('card_bank_id'):
            if method.collection_type != 'card' or method.card_bank_id.company_id != method.company_id:
                raise ValidationError(_('El banco del catálogo debe ser de la misma compañía y se usa en cobros con tarjeta.'))
        for method in self.filtered(lambda item: item.collection_type != 'standard'):
            account = method.outstanding_account_id
            if not method.company_id or method.journal_id.type != 'bank' or method.journal_id.company_id != method.company_id:
                raise ValidationError(_('Transferencia, cheque y terminal requieren un diario bancario de la misma compañía.'))
            if (not account or not account.active or not account.reconcile
                    or method.company_id not in account.company_ids
                    or account.account_type not in ('asset_current', 'asset_cash')):
                raise ValidationError(_('Selecciona una cuenta de cobros pendientes activa, conciliable y de la misma compañía.'))


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    terminal_bank_id = fields.Many2one('pos.card.bank', string='Banco de la terminal', ondelete='restrict', copy=False)

    @api.model
    def _load_pos_data_fields(self, config):
        names = super()._load_pos_data_fields(config)
        return names + ['terminal_bank_id'] if names and 'terminal_bank_id' not in names else names

    @api.constrains('terminal_bank_id', 'payment_method_id', 'pos_order_id')
    def _check_terminal_bank(self):
        for payment in self.filtered('terminal_bank_id'):
            method = payment.payment_method_id
            if (payment.terminal_bank_id.company_id != payment.company_id
                    or (method.collection_type != 'card' and method.card_payment_code not in ('04', '28'))):
                raise ValidationError(_('El banco de la terminal debe ser de la compañía del pago y usarse con tarjeta.'))


class PosConfigBankPayments(models.Model):
    _inherit = 'pos.config'

    def action_prepare_bank_payments(self):
        """Create native bank methods; native POS entries use outstanding_account_id."""
        self.ensure_one()
        if not self.env.user.has_group('point_of_sale.group_pos_manager'):
            raise UserError(_('Solo un administrador del Punto de Venta puede preparar estos métodos.'))
        self.check_access('write')
        if self.company_id not in self.env.companies:
            raise UserError(_('Selecciona la compañía del Punto de Venta.'))
        # The native method/journal locks also apply to later configuration changes.
        if self.env['pos.session'].search_count([('config_id', '=', self.id), ('state', '!=', 'closed')]):
            raise UserError(_('Cierra y valida la sesión de este Punto de Venta antes de preparar los métodos bancarios.'))
        with self.env.cr.savepoint():
            self.env['res.company']._with_locked_records(self.company_id)
            methods = self.env['pos.payment.method']
            for index, bank in enumerate(('BBVA', 'Santander')):
                company_env = self.with_company(self.company_id).env
                journals = company_env['account.journal']
                journal = journals.search([('name', '=', bank), ('type', '=', 'bank'),
                                           ('company_id', '=', self.company_id.id)], limit=1)
                if not journal:
                    prefix = 'BBV' if index == 0 else 'SAN'
                    code = prefix
                    number = 0
                    while journals.search_count([('code', '=', code), ('company_id', '=', self.company_id.id)]):
                        number += 1
                        code = '%s%s' % (prefix, number)
                    journal = journals.create({'name': bank, 'code': code, 'type': 'bank', 'company_id': self.company_id.id})
                for kind, label, sat in (
                        ('transfer', 'Transferencia', 'transferencia'),
                        ('check', 'Cheque', 'cheque'),
                        ('card', 'Terminal', 'tarjeta_debito')):
                    method = company_env['pos.payment.method'].search([
                        ('company_id', '=', self.company_id.id), ('journal_id', '=', journal.id),
                        ('collection_type', '=', kind), ('terminal_label', '=', bank)], limit=1)
                    if not method:
                        account = self._bank_pending_account(bank, index)
                        method = company_env['pos.payment.method'].create({
                            'name': '%s %s' % (label, bank), 'company_id': self.company_id.id,
                            'journal_id': journal.id, 'outstanding_account_id': account.id,
                            'collection_type': kind, 'terminal_label': bank,
                            'payment_method_type': 'none',
                            'l10n_mx_edi_payment_method_id': self.env.ref('l10n_mx_edi.payment_method_' + sat).id,
                        })
                    methods |= method
            self.write({'payment_method_ids': [Command.link(method.id) for method in methods]})
        return {
            'type': 'ir.actions.act_window', 'name': _('Transferencias, cheques y terminales'),
            'res_model': 'pos.payment.method', 'view_mode': 'list,form',
            'domain': [('id', 'in', methods.ids)], 'context': {},
        }

    def _bank_pending_account(self, bank, index):
        self.ensure_one()
        accounts = self.with_company(self.company_id).env['account.account']
        name = 'Cobros PDV %s pendientes de conciliar' % bank
        account = accounts.search([('name', '=', name), ('company_ids', 'in', self.company_id.ids),
                                   ('account_type', '=', 'asset_current'), ('reconcile', '=', True), ('active', '=', True)], limit=1)
        if not account:
            # Without an explicit cache, the core skips the start code even when it is free.
            code = accounts.with_context(active_test=False)._search_new_account_code(str(102990001 + index), cache=set())
            account = accounts.create({'name': name, 'code': code, 'account_type': 'asset_current',
                                       'reconcile': True, 'company_ids': [Command.set(self.company_id.ids)]})
        return account
