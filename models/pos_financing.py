from lxml import etree

from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError


PAYMENT_FLOWS = [
    ('standard', 'Pago completo'),
    ('ppd_partial', 'Pago inicial y saldo pendiente PPD'),
    ('credit', 'Crédito autorizado'),
]


class AccountPaymentTerm(models.Model):
    _name = 'account.payment.term'
    _inherit = ['account.payment.term', 'pos.load.mixin']

    def _is_pos_deferred_term(self, company, date):
        self.ensure_one()
        return (self.active and (not self.company_id or self.company_id == company)
                and any(line.value_amount > 0 and line._get_due_date(date) > date for line in self.line_ids))

    @api.model
    def _load_pos_data_domain(self, data, config):
        terms = self.search(['|', ('company_id', '=', False), ('company_id', '=', config.company_id.id)])
        today = fields.Date.context_today(self)
        return [('id', 'in', terms.filtered(lambda term: term._is_pos_deferred_term(config.company_id, today)).ids)]

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name']


class PosConfig(models.Model):
    _inherit = 'pos.config'

    ppd_payment_term_id = fields.Many2one(
        'account.payment.term', string='Plazo para saldos pendientes PPD', check_company=True,
        help='PPD es la política fiscal; este plazo determina el vencimiento de la deuda.',
    )

    @api.model
    def _load_pos_data_fields(self, config):
        names = super()._load_pos_data_fields(config)
        # An empty list tells Odoo to load all configuration fields.
        return list(dict.fromkeys(names + ['ppd_payment_term_id'])) if names else names

    def action_prepare_deferred_payments(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group('point_of_sale.group_pos_manager'):
            raise UserError(_('La preparación de pagos pendientes requiere permisos de administrador del POS.'))
        method = self.payment_method_ids.filtered(lambda value: value.type == 'pay_later')[:1]
        if not method:
            # Native type is computed (not stored); no journal means pay_later.
            method = self.env['pos.payment.method'].search([
                ('company_id', '=', self.company_id.id), ('journal_id', '=', False),
            ], limit=1)
            if not method:
                method = self.env['pos.payment.method'].create({
                    'name': 'Saldo pendiente / crédito', 'company_id': self.company_id.id,
                    'journal_id': False, 'split_transactions': True,
                })
            # Native opt-in for adding a method to a running session. Existing
            # methods, their journals and past payments remain untouched.
            self.with_context(bypass_payment_method_ids_forbidden_change=True).write({
                'payment_method_ids': [Command.link(method.id)],
            })
        if not self.ppd_payment_term_id:
            self.ppd_payment_term_id = self.env.ref('entintados_pdv.payment_term_pos_ppd')
        return True


class PosOrder(models.Model):
    _inherit = 'pos.order'

    pos_payment_flow = fields.Selection(PAYMENT_FLOWS, default='standard', required=True, copy=False)
    pos_payment_term_id = fields.Many2one('account.payment.term', string='Términos de pago', copy=False, check_company=True)

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == 'form':
            for header in arch.xpath("//group[@name='order_fields'][field[@name='partner_id']]"):
                partner = header.find("field[@name='partner_id']")
                split = header.index(partner) + 1
                children = list(header)
                header.set('col', '2')
                for name, nodes in (
                    ('order_customer_details', children[:split]),
                    ('order_billing_details', children[split:]),
                ):
                    etree.SubElement(header, 'group', name=name).extend(nodes)
        return arch, view

    @api.model_create_multi
    def create(self, vals_list):
        # Native/new or offline POS tickets can serialize an unset selection
        # as false instead of omitting it and using the Python default.
        vals_list = [dict(values, pos_payment_flow=values.get('pos_payment_flow') or 'standard')
                     for values in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if 'pos_payment_flow' in vals and not vals['pos_payment_flow']:
            vals = dict(vals, pos_payment_flow='standard')
        return super().write(vals)

    @api.model
    def _load_pos_data_fields(self, config):
        names = super()._load_pos_data_fields(config)
        return list(dict.fromkeys(names + ['pos_payment_flow', 'pos_payment_term_id'])) if names else names

    def _check_pos_deferred_payments(self, check_risk=False):
        for order in self:
            deferred = order.payment_ids.filtered(lambda payment: payment.payment_method_id.type == 'pay_later')
            amount = sum(deferred.mapped('amount'))
            explicit = order.pos_payment_flow in ('ppd_partial', 'credit')
            # Native collection of an old debt uses a negative pay_later entry.
            if amount <= 0 and not explicit:
                if check_risk and order._is_pos_cash_sale():
                    order._check_pos_cash_approval()
                continue
            real = order.payment_ids - deferred
            received = sum(real.mapped('amount'))
            if not order.partner_id or not order.to_invoice:
                raise UserError(_('Los saldos pendientes y el crédito requieren cliente identificado y factura.'))
            partner = order.partner_id.commercial_partner_id.sudo().with_company(order.company_id).with_context(
                allowed_company_ids=order.company_id.ids)
            if not partner.is_credit:
                raise UserError(_('Las opciones de pago diferido requieren un contacto con la condición Crédito habilitada.'))
            if order.amount_total <= 0 or amount <= 0 or any(payment.amount < 0 for payment in order.payment_ids):
                raise UserError(_('Esta modalidad requiere una venta positiva, sin devoluciones, con saldo pendiente positivo.'))
            if order.invoice_split_count > 1 or order.config_id.cash_rounding:
                raise UserError(_('Los pagos diferidos no admiten facturas divididas ni redondeo de efectivo.'))
            date = fields.Date.to_date(order.date_order) or fields.Date.context_today(order)
            if order.pos_payment_term_id and not order.pos_payment_term_id._is_pos_deferred_term(order.company_id, date):
                raise UserError(_('Selecciona un término de pago activo con vencimiento posterior al día de la venta.'))
            if order.currency_id.compare_amounts(received + amount, order.amount_total):
                raise UserError(_('El dinero recibido más el saldo pendiente debe coincidir con el total de la venta.'))
            if any(payment.payment_method_id not in order.config_id.payment_method_ids for payment in order.payment_ids):
                raise UserError(_('Utiliza únicamente métodos de pago habilitados en esta caja.'))
            if order.pos_payment_flow == 'ppd_partial':
                if received <= 0:
                    raise UserError(_('Registra primero un pago inicial real. Para financiar toda la venta utiliza Crédito autorizado.'))
            if not check_risk:
                continue
            self.env.cr.execute('SELECT id FROM res_partner WHERE id = %s FOR UPDATE', [partner.id])
            partner.invalidate_recordset()
            applied = -sum(order.lines.filtered(lambda line: line._accounting_advance_source()).mapped('price_subtotal_incl'))
            projected = order.config_id._credit_projection(partner, amount, applied, order=order)
            if (partner.credit_limit <= 0 or partner.risk_exception or partner._pos_has_overdue_balance()
                    or partner.risk_currency_id.compare_amounts(projected, partner.credit_limit) > 0):
                order._require_credit_approval(partner, amount, applied)

    def _is_pos_cash_sale(self):
        self.ensure_one()
        return (self.partner_id and self.amount_total > 0 and not self.is_refund
                and any(not (line.settled_invoice_id or line.settled_order_id) for line in self.lines)
                and not any(payment.amount and payment.payment_method_id.type == 'pay_later'
                            for payment in self.payment_ids))

    def _check_pos_cash_approval(self):
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id.sudo().with_company(self.company_id).with_context(
            allowed_company_ids=self.company_id.ids)
        self.env.cr.execute('SELECT id FROM res_partner WHERE id = %s FOR UPDATE', [partner.id])
        partner.invalidate_recordset()
        if partner._pos_cash_requires_approval():
            applied = -sum(self.lines.filtered(lambda line: line._accounting_advance_source()).mapped('price_subtotal_incl'))
            self._require_credit_approval(partner, 0, applied)

    def _prepare_invoice_vals(self):
        self._check_pos_deferred_payments()
        values = super()._prepare_invoice_vals()
        if any(payment.amount > 0 and payment.payment_method_id.type == 'pay_later' for payment in self.payment_ids):
            values['pos_financing_flow'] = 'ppd_partial' if self.pos_payment_flow == 'ppd_partial' else 'credit'
            term = self.pos_payment_term_id
            if not term:
                partner_term = self.partner_id.with_company(self.company_id).property_payment_term_id
                date = fields.Date.to_date(self.date_order) or fields.Date.context_today(self)
                term = (partner_term | self.config_id.ppd_payment_term_id).filtered(
                    lambda candidate: candidate._is_pos_deferred_term(self.company_id, date)
                )[:1] or self.env.ref('entintados_pdv.payment_term_pos_ppd')
            values['invoice_payment_term_id'] = term.id
            if 'l10n_mx_edi_payment_policy' in self.env['account.move']._fields:
                values['l10n_mx_edi_payment_policy'] = 'PPD'
        return values

    def _get_invoice_post_context(self):
        context = super()._get_invoice_post_context()
        approved = self.env['pos.credit.approval'].sudo().search_count([
            ('used_order_id', '=', self.id), ('state', '=', 'used'),
        ]) if isinstance(self.id, int) else 0
        if self.pos_payment_flow == 'ppd_partial' or approved:
            context = dict(context, bypass_risk=True)
        return context


class AccountMove(models.Model):
    _inherit = 'account.move'

    pos_financing_flow = fields.Selection(PAYMENT_FLOWS, default='standard', copy=False, readonly=True)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _pos_has_overdue_balance(self):
        self.ensure_one()
        return bool(self.env['account.move.line'].sudo().search_count([
            ('partner_id.commercial_partner_id', '=', self.id),
            ('company_id', '=', self.env.company.id),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('amount_residual', '>', 0),
            ('date_maturity', '<', fields.Date.context_today(self)),
        ], limit=1))

    def _pos_cash_requires_approval(self):
        self.ensure_one()
        return (self._pos_has_overdue_balance()
                or self.risk_currency_id.compare_amounts(self._pos_credit_exposure(), max(self.credit_limit, 0)) > 0)

    def _pos_credit_exposure(self, exclude_order=None, exclude_invoice=None):
        """Keep a debt floor even when optional OCA accounting categories are off."""
        self.ensure_one()
        total, = self.env['account.move.line'].sudo()._read_group([
            ('partner_id.commercial_partner_id', '=', self.commercial_partner_id.id),
            ('company_id', '=', self.env.company.id), ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', 'asset_receivable'), ('reconciled', '=', False),
        ], [], ['amount_residual:sum'])[0]
        ledger = self.env.company.currency_id._convert(
            total or 0, self.risk_currency_id, self.env.company, fields.Date.context_today(self))
        gross = self.risk_gross_total
        if exclude_invoice and self.risk_invoice_draft_include:
            for line in exclude_invoice.sudo().line_ids.filtered(
                lambda line: line.account_id.account_type == 'asset_receivable' and line.partner_id == self
            ):
                gross -= self._get_amount_in_risk_currency(
                    line.currency_id, line.amount_residual_currency, line.amount_residual,
                    line.account_id, line.company_id,
                )
        if (exclude_order and exclude_order.state in ('paid', 'done') and not exclude_order.account_move
                and self.env.company.advance_risk_enabled and exclude_order.session_id.state != 'closed'):
            own = sum(exclude_order.payment_ids.filtered(
                lambda payment: payment.payment_method_id.type == 'pay_later').mapped('amount'))
            gross -= exclude_order.currency_id._convert(
                max(own, 0), self.risk_currency_id, self.env.company, fields.Date.context_today(self))
        return max(gross, ledger, 0)
