import logging

from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = 'res.company'

    supplier_advance_account_id = fields.Many2one(
        'account.account', string='Cuenta de anticipos a proveedores', check_company=True,
        domain="[('account_type', '=', 'asset_current'), ('reconcile', '=', True)]",
    )
    advance_product_id = fields.Many2one(
        'product.product', string='Producto de anticipos', check_company=True,
        domain="[('type', '=', 'service')]",
    )
    advance_risk_enabled = fields.Boolean(
        string='Integrar anticipos con riesgo financiero',
        help='Considera únicamente el saldo de anticipos contabilizados, pagados y no aplicado ni reservado.',
    )

    def action_setup_advance_accounts(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group('account.group_account_manager'):
            raise UserError(_('La configuración de anticipos requiere permisos de administrador contable.'))
        with self.env.cr.savepoint():
            self._prepare_advance_defaults(activate=True)
        return True

    def _prepare_advance_defaults(self, activate=False):
        """Prepare all related values before writing the company constraints."""
        self.ensure_one()
        company = self.with_company(self)
        Account = self.env['account.account'].with_company(self)
        values = {}
        custom_configuration = bool(company.advance_product_id or company.supplier_advance_account_id)
        for field_name, code, name, account_type in (
            ('downpayment_account_id', 'ANTCLI', 'Anticipos de clientes', 'liability_current'),
            ('supplier_advance_account_id', 'ANTPRO', 'Anticipos a proveedores', 'asset_current'),
        ):
            account = company[field_name]
            compatible = (account and account.active and account.account_type == account_type
                          and account.reconcile and company in account.company_ids)
            if account and not compatible and custom_configuration:
                raise ValidationError(_(
                    'La cuenta %(account)s no es compatible con anticipos en %(company)s. '
                    'Revise su tipo y conciliación antes de preparar la configuración.',
                    account=account.display_name, company=company.display_name))
            if not compatible:
                # A native sales down-payment account may be an income account.
                # Keep that account intact and create/select a liability default.
                domain = [('code', '=', code), ('company_ids', 'in', company.id)]
                account = Account.search(domain + [('account_type', '=', account_type), ('reconcile', '=', True)], limit=1)
                if not account:
                    candidate, suffix = code, 0
                    while Account.with_context(active_test=False).search_count([
                            ('code', '=', candidate), ('company_ids', 'in', company.id)]):
                        suffix += 1
                        candidate = '%s%s' % (code, suffix)
                    account = Account.create({
                        'code': candidate, 'name': name, 'account_type': account_type,
                        'reconcile': True, 'company_ids': [Command.set(self.ids)],
                    })
            values[field_name] = account.id
        product = company.advance_product_id
        if not product:
            Product = self.env['product.product'].with_company(company)
            reference = 'ANTICIPO-%s' % company.id
            product = Product.search([
                ('default_code', '=', reference), ('company_id', '=', company.id),
                ('type', '=', 'service'), ('is_accounting_advance', '=', True),
                ('property_account_income_id', '=', values['downpayment_account_id']),
                ('property_account_expense_id', '=', values['supplier_advance_account_id']),
            ], limit=1)
            if not product:
                candidate, suffix = reference, 0
                while Product.with_context(active_test=False).search_count([('default_code', '=', candidate)]):
                    suffix += 1
                    candidate = '%s-%s' % (reference, suffix)
                product = Product.create({
                    'name': 'Anticipo', 'default_code': candidate,
                    'type': 'service', 'available_in_pos': True, 'company_id': self.id,
                    'is_accounting_advance': True,
                    'property_account_income_id': values['downpayment_account_id'],
                    'property_account_expense_id': values['supplier_advance_account_id'],
                    'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
                })
        values['advance_product_id'] = product.id
        if activate:
            values['advance_risk_enabled'] = True
        company.write(values)
        company._check_advance_configuration()
        boxes = self.env['pos.config'].with_context(active_test=False).search([
            ('company_id', '=', company.id), ('down_payment_product_id', '=', False)])
        for box in boxes:
            try:
                with self.env.cr.savepoint():
                    box.write({'down_payment_product_id': product.id})
            except (UserError, ValidationError) as error:
                _logger.warning('Advance product not assigned to POS %s: %s', box.id, error)
        return True

    @api.model
    def _setup_advance_defaults(self):
        """XML install/upgrade entry point; isolate conflicting legacy setups."""
        for company in self.sudo().search([]):
            try:
                with self.env.cr.savepoint():
                    company._prepare_advance_defaults(activate=not bool(company.advance_product_id))
            except (UserError, ValidationError) as error:
                _logger.warning('Automatic advance setup skipped for company %s: %s', company.id, error)
        return True

    @api.constrains('downpayment_account_id', 'supplier_advance_account_id', 'advance_product_id')
    def _check_advance_configuration(self):
        for company in self:
            for account, account_type in (
                (company.downpayment_account_id, 'liability_current'),
                (company.supplier_advance_account_id, 'asset_current'),
            ):
                if account and (account.account_type != account_type or not account.reconcile
                                or company not in account.company_ids):
                    # Only validate the native sales account once the integration is configured.
                    if company.supplier_advance_account_id or company.advance_product_id:
                        raise ValidationError(_('Las cuentas de anticipos deben ser conciliables, de la compañía y del tipo activo/pasivo circulante correspondiente.'))
            product = company.advance_product_id.with_company(company)
            if product and (not product.is_accounting_advance or product.type != 'service'
                            or product.property_account_income_id != company.downpayment_account_id
                            or product.property_account_expense_id != company.supplier_advance_account_id):
                raise ValidationError(_('El producto de anticipos debe ser un servicio y utilizar las cuentas de anticipos configuradas.'))


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    supplier_advance_account_id = fields.Many2one(related='company_id.supplier_advance_account_id', readonly=False)
    advance_product_id = fields.Many2one(related='company_id.advance_product_id', readonly=False)
    advance_risk_enabled = fields.Boolean(related='company_id.advance_risk_enabled', readonly=False)

    def action_setup_advance_accounts(self):
        self.ensure_one()
        self.company_id.action_setup_advance_accounts()
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_accounting_advance = fields.Boolean(string='Producto de anticipo')

    @api.model
    def _load_pos_data_fields(self, config):
        return list(dict.fromkeys(super()._load_pos_data_fields(config) + ['is_accounting_advance']))


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    advance_source_id = fields.Many2one(
        'account.move', string='Factura de anticipo aplicada', copy=False,
        check_company=True, ondelete='restrict', index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # Native sales deductions share the original down-payment sale line.
        for line in lines.filtered(lambda item: item.is_downpayment and item.price_total < 0
                                   and item.company_id.advance_risk_enabled and not item.advance_source_id):
            sources = line.sale_line_ids._accounting_advance_sources() - line.move_id
            if len(sources) == 1:
                line.advance_source_id = sources
        return lines

    def write(self, vals):
        if 'advance_source_id' in vals and any(line.move_id.state != 'draft' for line in self):
            raise UserError(_('La relación del anticipo solo puede modificarse en un documento en borrador.'))
        return super().write(vals)


class AccountMove(models.Model):
    _inherit = 'account.move'

    advance_document = fields.Boolean(string='Factura de anticipo', copy=False)
    advance_fiscal_kind = fields.Selection([
        ('on_account', 'Pago a cuenta de una operación definida'),
        ('fiscal', 'Anticipo fiscal (operación aún no definida)'),
    ], default='on_account', string='Tratamiento del anticipo', copy=False)
    advance_purchase_id = fields.Many2one('purchase.order', string='Compra del anticipo', check_company=True, copy=False)
    advance_application_line_ids = fields.One2many('account.move.line', 'advance_source_id')
    advance_target_id = fields.Many2one('account.move', string='Factura a liquidar con la aplicación', check_company=True, copy=False)
    is_advance_invoice = fields.Boolean(compute='_compute_is_advance_invoice', store=True)
    advance_paid = fields.Monetary(compute='_compute_advance_balance', string='Anticipo pagado')
    advance_applied = fields.Monetary(compute='_compute_advance_balance', string='Anticipo aplicado')
    advance_reserved = fields.Monetary(compute='_compute_advance_balance', string='Reservado en borradores')
    advance_available = fields.Monetary(compute='_compute_advance_balance', string='Anticipo disponible')
    advance_source_ids = fields.Many2many('account.move', 'advance_source_display_rel', 'move_id', 'source_id', compute='_compute_advance_links', string='Anticipos aplicados')
    advance_final_ids = fields.Many2many('account.move', 'advance_final_display_rel', 'move_id', 'final_id', compute='_compute_advance_links', string='Documentos de aplicación')
    advance_link_count = fields.Integer(compute='_compute_advance_links')

    @api.depends('advance_document', 'invoice_line_ids.is_downpayment',
                 'invoice_line_ids.product_id.is_accounting_advance', 'invoice_line_ids.price_total', 'move_type')
    def _compute_is_advance_invoice(self):
        for move in self:
            products = move.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
            move.is_advance_invoice = move.move_type in ('in_invoice', 'out_invoice') and (
                move.advance_document or bool(products) and all(
                    (line.is_downpayment or line.product_id.is_accounting_advance)
                    and line.price_total > 0 for line in products
                )
            )

    def _advance_application_lines(self):
        self.ensure_one()
        sale_lines = (self.invoice_line_ids.sale_line_ids | self.pos_order_ids.lines.sale_order_line_id).filtered('is_downpayment')
        native_lines = sale_lines.invoice_lines.filtered(
            lambda line: line.move_id != self and (
                line.price_total < 0 or line.move_id.move_type == 'out_refund'
            )
        )
        return self.advance_application_line_ids | native_lines

    def _advance_account(self):
        self.ensure_one()
        account = (self.company_id.supplier_advance_account_id if self.move_type == 'in_invoice'
                   else self.company_id.downpayment_account_id)
        if account and self.fiscal_position_id:
            account = self.fiscal_position_id.map_account(account)
        return account

    def _uses_advance_account(self):
        self.ensure_one()
        account = self._advance_account()
        products = self.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
        return bool(account and products and all(line.account_id == account for line in products))

    @api.depends('is_advance_invoice', 'state', 'amount_total', 'amount_residual',
                 'invoice_line_ids.account_id', 'fiscal_position_id',
                 'company_id.downpayment_account_id', 'company_id.supplier_advance_account_id',
                 'invoice_line_ids.sale_line_ids.invoice_lines.price_total',
                 'invoice_line_ids.sale_line_ids.invoice_lines.move_id.state',
                 'invoice_line_ids.sale_line_ids.invoice_lines.move_id.move_type',
                 'pos_order_ids.lines.sale_order_line_id.invoice_lines.price_total',
                 'pos_order_ids.lines.sale_order_line_id.invoice_lines.move_id.state',
                 'advance_application_line_ids.price_total', 'advance_application_line_ids.move_id.state',
                 'advance_application_line_ids.move_id.move_type')
    def _compute_advance_balance(self):
        for move in self:
            paid = max(move.amount_total - move.amount_residual, 0) if move.state == 'posted' and move.is_advance_invoice and move._uses_advance_account() else 0
            applied = reserved = 0
            for line in move._advance_application_lines():
                sign = 1 if line.move_id.move_type in ('out_refund', 'in_refund') else -1
                amount = sign * line.price_total
                if line.move_id.state == 'posted':
                    applied += amount
                elif line.move_id.state == 'draft':
                    reserved += amount
            move.advance_paid = paid
            move.advance_applied = applied
            move.advance_reserved = reserved
            move.advance_available = max(paid - applied - reserved, 0)

    @api.depends('invoice_line_ids.advance_source_id', 'advance_application_line_ids.move_id',
                 'invoice_line_ids.sale_line_ids.invoice_lines.move_id',
                 'invoice_line_ids.sale_line_ids.invoice_lines.price_total',
                 'invoice_line_ids.sale_line_ids.pos_order_line_ids.order_id.account_move',
                 'pos_order_ids.lines.sale_order_line_id.invoice_lines.move_id')
    def _compute_advance_links(self):
        for move in self:
            deductions = move.invoice_line_ids.filtered(lambda line: line.is_downpayment and
                                                        (line.price_total < 0 or move.move_type == 'out_refund'))
            native_sources = deductions.sale_line_ids._accounting_advance_sources() - move
            move.advance_source_ids = move.invoice_line_ids.advance_source_id | native_sources
            move.advance_final_ids = move._advance_application_lines().move_id if move.is_advance_invoice else move.advance_application_line_ids.move_id
            move.advance_link_count = len(move.advance_source_ids | move.advance_final_ids)

    def action_view_advance_links(self):
        self.ensure_one()
        return self._action_open_moves(self.advance_source_ids | self.advance_final_ids, _('Documentos de anticipos'))

    def action_apply_advance(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'account.advance.apply',
            'view_mode': 'form', 'target': 'new', 'name': _('Aplicar anticipo'),
            'context': {'default_invoice_id': self.id},
        }

    def _lock_advance_balances(self):
        if self:
            self.flush_recordset()
            self.env.cr.execute('SELECT id FROM account_move WHERE id IN %s ORDER BY id FOR NO KEY UPDATE', [tuple(self.ids)])
            # A competing repeatable-read transaction must retry with a fresh snapshot.
            self.env.cr.execute('UPDATE account_move SET write_date = write_date WHERE id IN %s', [tuple(self.ids)])
            self.invalidate_recordset(['advance_application_line_ids', 'advance_paid', 'advance_applied', 'advance_reserved', 'advance_available'])

    @api.constrains('is_advance_invoice', 'move_type', 'l10n_mx_edi_payment_policy')
    def _check_customer_advance_payment_policy(self):
        if any(move.move_type == 'out_invoice' and move.is_advance_invoice
               and move.l10n_mx_edi_payment_policy == 'PPD' for move in self):
            raise UserError(_('Los anticipos de clientes solo se reciben de contado y su factura debe ser PUE.'))

    def _check_advance_applications(self):
        self._check_customer_advance_payment_policy()
        self.invoice_line_ids.advance_source_id._lock_advance_balances()
        for move in self:
            if move.move_type == 'out_invoice' and move.is_advance_invoice:
                date = move.invoice_date or fields.Date.context_today(move)
                if ((move.invoice_date_due and move.invoice_date_due > date)
                        or len(move.invoice_payment_term_id.line_ids) > 1):
                    raise UserError(_('La factura de anticipo requiere una condición de pago inmediata, de contado.'))
            if move.is_advance_invoice and (move.advance_document or move.company_id.advance_risk_enabled):
                if move.currency_id.compare_amounts(move.amount_total, 0) <= 0:
                    raise UserError(_('La factura de anticipo debe tener un importe positivo.'))
                account = (move.company_id.supplier_advance_account_id if move.move_type == 'in_invoice'
                           else move.company_id.downpayment_account_id)
                if not account or not move._uses_advance_account():
                    raise UserError(_('La factura de anticipo debe utilizar la cuenta de anticipos configurada.'))
            if move.company_id.advance_risk_enabled:
                unlinked = move.invoice_line_ids.filtered(
                    lambda line: line.price_total < 0 and (line.is_downpayment or line.product_id.is_accounting_advance)
                    and not line.advance_source_id
                )
                if unlinked:
                    raise UserError(_('Vincula las deducciones con su factura de anticipo original antes de contabilizar.'))
            for line in move.invoice_line_ids.filtered('advance_source_id'):
                source = line.advance_source_id
                if (source.state != 'posted' or not source.is_advance_invoice
                        or source.company_id != move.company_id or source.currency_id != move.currency_id
                        or source.commercial_partner_id != move.commercial_partner_id
                        or source.move_type != ('in_invoice' if move.move_type in ('in_invoice', 'in_refund') else 'out_invoice')):
                    raise UserError(_('El anticipo debe estar contabilizado y corresponder al mismo contacto comercial, compañía, moneda y tipo de operación.'))
                account = source._advance_account()
                if not account or line.account_id != account:
                    raise UserError(_('La aplicación debe utilizar la cuenta de anticipos configurada.'))
                sign = 1 if move.move_type in ('out_refund', 'in_refund') else -1
                if move.currency_id.compare_amounts(sign * line.price_total, 0) <= 0:
                    raise UserError(_('La aplicación debe reducir el saldo del anticipo.'))
                if source.currency_id.compare_amounts(source.advance_applied + source.advance_reserved, source.advance_paid) > 0:
                    raise UserError(_('El importe aplicado y reservado supera el anticipo pagado de %s.') % source.display_name)
            if move.is_advance_invoice and move.advance_paid and move.advance_applied:
                if move.currency_id.compare_amounts(move.advance_applied, move.advance_paid) > 0:
                    raise UserError(_('No puedes retirar un pago de un anticipo ya aplicado.'))

    def _post(self, soft=True):
        for move in self.filtered(lambda item: item.move_type in ('out_refund', 'in_refund')
                                 and item.reversed_entry_id.is_advance_invoice):
            move.invoice_line_ids.filtered(lambda line: line.display_type == 'product').advance_source_id = move.reversed_entry_id
        self._check_advance_applications()
        for move in self:
            fiscal_sources = move.invoice_line_ids.advance_source_id.filtered(lambda source: source.advance_fiscal_kind == 'fiscal')
            if fiscal_sources and (move.move_type in ('in_invoice', 'out_invoice') or move.advance_target_id):
                origins = move.advance_target_id if move.move_type in ('in_refund', 'out_refund') else fiscal_sources
                uuids = [getattr(origin, 'l10n_mx_edi_cfdi_uuid', False) for origin in origins]
                if not uuids or not all(uuids) or 'l10n_mx_edi_cfdi_origin' not in move._fields:
                    raise UserError(_('La aplicación fiscal requiere los UUID de los CFDI de origen y la localización mexicana.'))
                existing = move.l10n_mx_edi_cfdi_origin or ''
                if existing and not existing.startswith('07|'):
                    raise UserError(_('La relación fiscal existente no corresponde a aplicación de anticipo (07).'))
                old_uuids = existing[3:].split(',') if existing else []
                relation = '07|' + ','.join(sorted(set(uuid.strip() for uuid in old_uuids + uuids)))
                if existing != relation:
                    move.l10n_mx_edi_cfdi_origin = relation
        result = super()._post(soft)
        for move in self.filtered(lambda item: item.state == 'posted' and item.advance_target_id):
            target = move.advance_target_id
            if (target.state != 'posted' or target.move_type not in ('in_invoice', 'out_invoice')
                    or target.company_id != move.company_id or target.currency_id != move.currency_id
                    or target.commercial_partner_id != move.commercial_partner_id
                    or move.move_type != ('in_refund' if target.move_type == 'in_invoice' else 'out_refund')):
                raise UserError(_('La nota de aplicación debe corresponder a la factura final contabilizada.'))
            lines = (move | target).line_ids.filtered(
                lambda line: line.account_id.account_type in ('asset_receivable', 'liability_payable') and not line.reconciled)
            for account in lines.account_id:
                account_lines = lines.filtered(lambda line: line.account_id == account)
                if account_lines.filtered(lambda line: line.move_id == move) and account_lines.filtered(lambda line: line.move_id == target):
                    account_lines.reconcile()
        return result

    def button_draft(self):
        if any(move._advance_application_lines().filtered(lambda line: line.move_id.state != 'cancel')
               for move in self.filtered('is_advance_invoice')):
            raise UserError(_('Cancela primero los documentos que aplican este anticipo antes de restablecerlo a borrador.'))
        return super().button_draft()

    def write(self, vals):
        protected = {'advance_document', 'advance_fiscal_kind', 'advance_purchase_id', 'advance_target_id'}
        if protected.intersection(vals) and any(move.state != 'draft' for move in self):
            raise UserError(_('Los datos del anticipo solo pueden modificarse en borrador.'))
        return super().write(vals)


class AccountPartialReconcile(models.Model):
    _inherit = 'account.partial.reconcile'

    def unlink(self):
        sources = (self.debit_move_id.move_id | self.credit_move_id.move_id).filtered('is_advance_invoice')
        if sources:
            self.env.cr.execute('SELECT id FROM account_move WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(sources.ids)])
        result = super().unlink()
        for source in sources.exists():
            if source.currency_id.compare_amounts(source.advance_applied + source.advance_reserved, source.advance_paid) > 0:
                raise UserError(_('Cancela primero las aplicaciones del anticipo antes de retirar su pago.'))
        return result


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model
    def _load_pos_data_fields(self, config):
        names = super()._load_pos_data_fields(config)
        return list(dict.fromkeys(names + ['customer_advance_available'])) if names else names

    @api.model
    def _load_pos_data_read(self, records, config):
        values = super()._load_pos_data_read(records, config)
        for value in values:
            partner = records.browse(value['id']).with_company(config.company_id).with_context(
                allowed_company_ids=config.company_id.ids)
            value['customer_advance_available'] = partner.risk_currency_id._convert(
                partner.customer_advance_available, config.currency_id, config.company_id,
                fields.Date.context_today(partner))
        return values

    pos_risk_order_ids = fields.One2many('pos.order', 'partner_id')

    customer_advance_available = fields.Monetary(
        compute='_compute_advance_risk', currency_field='risk_currency_id',
        string='Anticipos de cliente disponibles', compute_sudo=True,
    )
    supplier_advance_available = fields.Monetary(
        compute='_compute_advance_risk', currency_field='risk_currency_id',
        string='Anticipos a proveedor disponibles', compute_sudo=True,
    )
    risk_gross_total = fields.Monetary(
        compute='_compute_risk_exception', currency_field='risk_currency_id', string='Riesgo crediticio',
    )
    risk_pos_open = fields.Monetary(compute='_compute_advance_risk', currency_field='risk_currency_id',
                                    string='Crédito POS pendiente de contabilizar', compute_sudo=True)
    risk_pos_open_include = fields.Boolean(compute='_compute_advance_risk', compute_sudo=True)
    risk_pos_open_limit = fields.Monetary(currency_field='risk_currency_id')
    advance_purchase_power = fields.Monetary(
        compute='_compute_advance_purchase_power', currency_field='risk_currency_id',
        string='Crédito disponible',
        help='Límite de crédito menos deuda crediticia. Los anticipos disponibles se controlan por separado.',
    )

    @api.depends('move_line_ids.move_id.amount_residual', 'move_line_ids.move_id.state',
                 'move_line_ids.move_id.advance_available', 'child_ids.move_line_ids.move_id.advance_available',
                 'pos_risk_order_ids.state', 'pos_risk_order_ids.payment_ids.amount',
                 'pos_risk_order_ids.account_move', 'pos_risk_order_ids.session_id.state',
                 'child_ids.pos_risk_order_ids.state', 'child_ids.pos_risk_order_ids.payment_ids.amount',
                 'risk_currency_id')
    @api.depends_context('company', 'allowed_company_ids')
    def _compute_advance_risk(self):
        commercial_ids = self.commercial_partner_id.ids
        sources = self.env['account.move'].search([
            ('commercial_partner_id', 'in', commercial_ids), ('state', '=', 'posted'),
            ('is_advance_invoice', '=', True), ('company_id', 'in', self.env.companies.ids),
            ('company_id.advance_risk_enabled', '=', True),
        ])
        pos_orders = self.env['pos.order'].search([
            ('partner_id.commercial_partner_id', 'in', commercial_ids),
            ('company_id', 'in', self.env.companies.ids), ('company_id.advance_risk_enabled', '=', True),
            ('state', 'in', ['paid', 'done']), ('account_move', '=', False),
            ('session_id.state', '!=', 'closed'),
        ])
        for partner in self:
            customer = supplier = 0
            for source in sources.filtered(lambda move: move.commercial_partner_id == partner.commercial_partner_id):
                value = source.currency_id._convert(
                    source.advance_available, partner.risk_currency_id, source.company_id,
                    fields.Date.context_today(partner),
                )
                if source.move_type == 'out_invoice':
                    customer += value
                else:
                    supplier += value
            partner.customer_advance_available = customer
            partner.supplier_advance_available = supplier
            pos_amount = 0
            for order in pos_orders.filtered(lambda item: item.partner_id.commercial_partner_id == partner.commercial_partner_id):
                amount = (order.customer_due_total if 'customer_due_total' in order._fields else
                          sum(order.payment_ids.filtered(lambda payment: payment.payment_method_id.type == 'pay_later').mapped('amount')))
                pos_amount += order.currency_id._convert(amount, partner.risk_currency_id, order.company_id, fields.Date.context_today(partner))
            partner.risk_pos_open = max(pos_amount, 0)
            partner.risk_pos_open_include = any(self.env.companies.mapped('advance_risk_enabled'))

    @api.model
    def _risk_field_list(self):
        return super()._risk_field_list() + [('risk_pos_open', 'risk_pos_open_limit', 'risk_pos_open_include')]

    @api.model
    def _get_depends_compute_risk_exception(self):
        return super()._get_depends_compute_risk_exception() + ['customer_advance_available']

    @api.depends(lambda self: self._get_depends_compute_risk_exception())
    @api.depends_context('company', 'allowed_company_ids')
    def _compute_risk_exception(self):
        super()._compute_risk_exception()
        for partner in self:
            gross = partner.risk_total
            partner.risk_gross_total = gross

    @api.depends('risk_gross_total', 'credit_limit')
    def _compute_advance_purchase_power(self):
        for partner in self:
            partner.advance_purchase_power = max(
                partner.sudo().credit_limit - partner.risk_gross_total, 0,
            )

    def action_view_available_advances(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'account.move', 'view_mode': 'list,form',
            'name': _('Anticipos'), 'domain': [
                ('commercial_partner_id', '=', self.commercial_partner_id.id),
                ('is_advance_invoice', '=', True), ('company_id', 'in', self.env.companies.ids),
            ],
        }

    def get_advance_pos_info(self, config_id):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise UserError(_('Esta consulta requiere permisos de Punto de Venta.'))
        config = self.env['pos.config'].browse(config_id).exists()
        config.check_access('read')
        if not config or config.company_id not in self.env.companies:
            raise UserError(_('El punto de venta no pertenece a las compañías permitidas.'))
        sources = self.env['account.move'].sudo().search([
            ('company_id', '=', config.company_id.id), ('commercial_partner_id', '=', self.commercial_partner_id.id),
            ('currency_id', '=', config.currency_id.id), ('move_type', '=', 'out_invoice'),
            ('is_advance_invoice', '=', True), ('state', '=', 'posted'),
        ])
        partner = self.commercial_partner_id.sudo().with_company(config.company_id).with_context(allowed_company_ids=config.company_id.ids)
        rate_date = fields.Date.context_today(self)
        def convert(amount):
            return partner.risk_currency_id._convert(amount, config.currency_id, config.company_id, rate_date)
        return {
            'has_outstanding_invoices': bool(self.env['account.move'].sudo().search_count([
                ('company_id', '=', config.company_id.id), ('commercial_partner_id', '=', partner.id),
                ('currency_id', '=', config.currency_id.id), ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'), ('pos_amount_unsettled', '>', 0),
                ('l10n_mx_edi_cfdi_state', 'not in', ['cancel', 'cancel_requested']),
            ], limit=1)),
            'advance_available': sum(sources.mapped('advance_available')), 'credit_limited': bool(partner.credit_limit),
            'credit_limit': convert(partner.credit_limit), 'risk': convert(partner.risk_total),
            'credit_authorized': bool(partner.is_credit), 'risk_exception': bool(partner.risk_exception),
            'purchase_power': convert(max(partner.credit_limit - partner._pos_credit_exposure(), 0)),
            'sources': [{'id': source.id, 'name': source.name, 'available': source.advance_available,
                         'fiscal_kind': source.advance_fiscal_kind, 'total': source.amount_total,
                         'lines': [{'quantity': line.quantity, 'price_unit': line.price_unit,
                                    'discount': line.discount, 'tax_ids': line.tax_ids.ids}
                                   for line in source.invoice_line_ids if line.display_type == 'product']}
                        for source in sources if source.advance_available > 0],
        }


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    advance_invoice_ids = fields.One2many('account.move', 'advance_purchase_id', string='Facturas de anticipo')
    advance_invoice_count = fields.Integer(compute='_compute_advance_invoice_count')

    @api.depends('advance_invoice_ids')
    def _compute_advance_invoice_count(self):
        for order in self:
            order.advance_invoice_count = len(order.advance_invoice_ids)

    def action_create_advance_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'purchase.advance.invoice',
            'view_mode': 'form', 'target': 'new', 'name': _('Registrar factura de anticipo del proveedor'),
            'context': {'default_purchase_id': self.id},
        }

    def action_view_advance_invoices(self):
        self.ensure_one()
        return self.env['account.move']._action_open_moves(self.advance_invoice_ids, _('Anticipos de proveedor'))


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _accounting_advance_sources(self):
        """Native POS deposits use pos_order_line_ids, not invoice_lines.

        Keep those links native: adding sale_line_ids to a POS invoice would
        count its invoiced quantities twice in pos_sale.
        """
        lines = self.sudo().filtered('is_downpayment')
        sources = lines.invoice_lines.filtered(lambda line: line.price_total > 0).move_id
        sources |= lines.sudo().pos_order_line_ids.filtered(
            lambda line: line.price_subtotal_incl > 0
        ).order_id.account_move
        return sources.filtered(lambda move: move.state == 'posted' and move.is_advance_invoice)
