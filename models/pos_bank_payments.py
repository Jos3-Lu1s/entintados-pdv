from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Compatibility field: company reads no longer require a custom SQL column.
    pos_card_default_journal_id = fields.Many2one('account.journal', string='Diario predeterminado para tarjetas',
        compute='_compute_pos_card_default_journal', inverse='_inverse_pos_card_default_journal',
        check_company=True, domain="[('type', '=', 'bank'), ('company_id', '=', id)]")

    def init(self):
        super().init()
        # Preserve previously configured values without requiring the legacy column.
        self.env.cr.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'res_company'
              AND column_name = 'pos_card_default_journal_id'
        """)
        if self.env.cr.fetchone():
            self.env.cr.execute("""
                INSERT INTO ir_config_parameter (key, value)
                SELECT 'entintados_pdv.pos_card_journal.' || id, pos_card_default_journal_id::text
                FROM res_company WHERE pos_card_default_journal_id IS NOT NULL
                ON CONFLICT (key) DO NOTHING
            """)

    def _compute_pos_card_default_journal(self):
        params = self.env['ir.config_parameter'].sudo()
        for company in self:
            value = params.get_param('entintados_pdv.pos_card_journal.%s' % company.id, '')
            company.pos_card_default_journal_id = (
                self.env['account.journal'].browse(int(value)).exists() if value.isdigit() else False)

    def _inverse_pos_card_default_journal(self):
        params = self.env['ir.config_parameter'].sudo()
        for company in self:
            params.set_param('entintados_pdv.pos_card_journal.%s' % company.id,
                             str(company.pos_card_default_journal_id.id or 0))

    def _payment_method_defaults(self, journal=None):
        self.ensure_one()
        journal = journal or self.pos_card_default_journal_id
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
    root_company_id = fields.Many2one(related='company_id.root_id')
    payment_method_ids = fields.One2many('pos.payment.method', 'card_bank_id', string='Métodos de pago')
    excluded_company_ids = fields.Many2many(
        'res.company', string='Sucursales excluidas',
        help='Deja este campo vacío para habilitar el banco en todas las sucursales de la empresa.',
        domain="[('id', 'child_of', root_company_id)]")
    active = fields.Boolean(default=True)
    journal_id = fields.Many2one('account.journal', string='Diario específico', check_company=True,
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]",
        help='Si queda vacío, se usa el diario predeterminado para tarjetas de la compañía.')

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('company_id', 'child_of', config.company_id.root_id.id), ('active', '=', True),
                ('excluded_company_ids', 'not in', config.company_id.ids)]

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name', 'active']

    @api.constrains('journal_id', 'company_id')
    def _check_card_journal(self):
        for bank in self:
            if bank.journal_id and (bank.journal_id.type != 'bank' or bank.journal_id.company_id != bank.company_id):
                raise ValidationError(_('Selecciona un diario bancario de la misma compañía.'))

    @api.constrains('excluded_company_ids', 'company_id')
    def _check_excluded_companies(self):
        for bank in self:
            companies = bank.excluded_company_ids | bank.sudo().payment_method_ids.company_id
            if companies.filtered(lambda company: company.root_id != bank.company_id.root_id):
                raise ValidationError(_('Selecciona únicamente sucursales de la misma empresa.'))

    def action_prepare_card_payment_methods(self):
        self.ensure_one()
        if not self.env.user.has_group('point_of_sale.group_pos_manager'):
            raise UserError(_('Solo un administrador del Punto de Venta puede preparar métodos de tarjeta.'))
        self.check_access('write')
        if not self.active:
            raise UserError(_('Activa el banco antes de preparar su método de pago.'))
        with self.env.cr.savepoint():
            methods = self._sync_card_payment_methods()
        accessible_companies = methods.company_id & self.env.user.company_ids
        return {
            'type': 'ir.actions.act_window', 'name': _('Métodos de tarjeta de %s', self.name),
            'res_model': 'pos.payment.method', 'view_mode': 'list,form',
            'domain': [('id', 'in', methods.filtered(lambda method: method.company_id in accessible_companies).ids)],
            'context': dict(self.env.context, allowed_company_ids=(self.env.companies | accessible_companies).ids),
        }

    def write(self, vals):
        if 'excluded_company_ids' not in vals and 'active' not in vals:
            return super().write(vals)
        with self.env.cr.savepoint():
            result = super().write(vals)
            for bank in self:
                if self.env['pos.payment.method'].sudo().with_context(active_test=False).search_count(
                        [('card_bank_id', '=', bank.id)], limit=1):
                    bank._sync_card_payment_methods()
            return result

    def _sync_card_payment_methods(self):
        self.ensure_one()
        self.env['res.company']._with_locked_records(self.company_id.root_id)
        configs = self.env['pos.config'].sudo().search([('company_id', 'child_of', self.company_id.root_id.id)])
        return self._apply_card_payment_methods(configs)

    def _apply_card_payment_methods(self, configs):
        active_configs = configs.filtered(lambda config: self.active and config.company_id not in self.excluded_company_ids)
        methods = self.env['pos.payment.method'].sudo()
        linked = methods.with_context(active_test=False).search([
            ('card_bank_id', '=', self.id), ('collection_type', '=', 'card')])
        for config in configs - active_configs:
            removing = config.payment_method_ids & linked
            if removing:
                config.write({'payment_method_ids': [Command.unlink(method.id) for method in removing]})
        prepared = methods.browse()
        for company in active_configs.company_id:
            destinations = active_configs.filtered(lambda config: config.company_id == company)
            company_methods = linked.filtered(lambda item: item.company_id == company)
            method = company_methods.filtered('active')[:1] or company_methods[:1]
            if not method:
                method = self._prepare_company_card_method(company, destinations[0], methods.with_company(company))
            elif not method.active:
                method.active = True
            for config in destinations:
                if method not in config.payment_method_ids:
                    config.write({'payment_method_ids': [Command.link(method.id)]})
            prepared |= method
        return prepared

    def _prepare_company_card_method(self, company, config, methods):
        legacy = methods.search([
            ('company_id', '=', company.id), ('collection_type', '=', 'card'),
            ('card_bank_id', '=', False), ('terminal_label', '=ilike', self.name),
        ], limit=2)
        if len(legacy) == 1:
            legacy.card_bank_id = self.id
            return legacy
        journal = self.journal_id if self.journal_id.company_id == company else None
        defaults = company._payment_method_defaults(journal)
        if not defaults['journal_id']:
            raise UserError(_('Configura el diario predeterminado para tarjetas de %(branch)s en Punto de Venta.',
                              branch=company.display_name))
        if not defaults['outstanding_account_id']:
            defaults['outstanding_account_id'] = config._bank_pending_account(self.name, 0).id
        return methods.create({
            **defaults, 'name': _('Tarjeta %s', self.name), 'company_id': company.id,
            'collection_type': 'card', 'card_bank_id': self.id, 'payment_method_type': 'none',
            'l10n_mx_edi_payment_method_id': self.env.ref('l10n_mx_edi.payment_method_tarjeta_debito').id,
        })


class PosBankPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    collection_type = fields.Selection([
        ('standard', 'Otro'), ('transfer', 'Transferencia'),
        ('check', 'Cheque'), ('card', 'Tarjeta / terminal'),
    ], string='Tipo de cobro', default='standard', required=True)
    terminal_label = fields.Char(string='Banco / terminal',
                                 help='Nombre visible en caja, por ejemplo BBVA o Santander.')
    card_bank_id = fields.Many2one('pos.card.bank', string='Banco de tarjeta', ondelete='restrict')
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
            if method.collection_type != 'card' or method.card_bank_id.company_id.root_id != method.company_id.root_id:
                raise ValidationError(_('El banco del catálogo debe ser de la misma empresa y se usa en cobros con tarjeta.'))
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

    @api.constrains('terminal_bank_id', 'payment_method_id', 'pos_order_id', 'amount')
    def _check_terminal_bank(self):
        for payment in self:
            method = payment.payment_method_id
            bank = payment.terminal_bank_id or method.card_bank_id
            if bank and (bank.company_id.root_id != payment.company_id.root_id
                         or (method.collection_type != 'card' and method.card_payment_code not in ('04', '28'))
                         or (payment.amount > 0 and payment.company_id in bank.excluded_company_ids)):
                raise ValidationError(_('El banco debe estar habilitado para cobros con tarjeta en la sucursal del pago.'))


class PosConfigBankPayments(models.Model):
    _inherit = 'pos.config'

    @api.model_create_multi
    def create(self, vals_list):
        configs = super().create(vals_list)
        for root in configs.company_id.root_id:
            self.env['res.company']._with_locked_records(root)
            banks = self.env['pos.card.bank'].sudo().search([
                ('company_id', 'child_of', root.id), ('payment_method_ids', '!=', False)])
            for bank in banks:
                bank._apply_card_payment_methods(configs.sudo().filtered(lambda config: config.company_id.root_id == root))
        return configs

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
