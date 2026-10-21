from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError


PAYMENT_FLOWS = [
    ('standard', 'Pago completo'),
    ('ppd_partial', 'Pago inicial y saldo pendiente PPD'),
    ('credit', 'Crédito autorizado'),
]


class PosConfig(models.Model):
    _inherit = 'pos.config'

    ppd_payment_term_id = fields.Many2one(
        'account.payment.term', string='Plazo para saldos pendientes PPD', check_company=True,
        help='PPD es la política fiscal; este plazo determina el vencimiento de la deuda.',
    )

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
        return list(dict.fromkeys(names + ['pos_payment_flow'])) if names else names

    def _check_pos_deferred_payments(self, check_risk=False):
        for order in self:
            deferred = order.payment_ids.filtered(lambda payment: payment.payment_method_id.type == 'pay_later')
            amount = sum(deferred.mapped('amount'))
            explicit = order.pos_payment_flow in ('ppd_partial', 'credit')
            # Native collection of an old debt uses a negative pay_later entry.
            if amount <= 0 and not explicit:
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
            if order.currency_id.compare_amounts(received + amount, order.amount_total):
                raise UserError(_('El dinero recibido más el saldo pendiente debe coincidir con el total de la venta.'))
            if any(payment.payment_method_id not in order.config_id.payment_method_ids for payment in order.payment_ids):
                raise UserError(_('Utiliza únicamente métodos de pago habilitados en esta caja.'))
            if order.pos_payment_flow == 'ppd_partial':
                if received <= 0:
                    raise UserError(_('Registra primero un pago inicial real. Para financiar toda la venta utiliza Crédito autorizado.'))
                # Initial-payment PPD still requires the customer's credit flag.
                continue
            if not check_risk:
                continue
            self.env.cr.execute('SELECT id FROM res_partner WHERE id = %s FOR UPDATE', [partner.id])
            partner.invalidate_recordset()
            if not partner.is_credit or partner.credit_limit <= 0:
                raise UserError(_('El cliente necesita crédito autorizado y un límite positivo antes de financiar la venta.'))
            applied = -sum(order.lines.filtered(lambda line: line._accounting_advance_source()).mapped('price_subtotal_incl'))
            remaining = max(partner.customer_advance_available - order.currency_id._convert(
                applied, partner.risk_currency_id, order.company_id, fields.Date.context_today(order)), 0)
            extra = order.currency_id._convert(amount, partner.risk_currency_id, order.company_id, fields.Date.context_today(order))
            if partner.risk_exception or max(partner._pos_credit_exposure() + extra - remaining, 0) > partner.credit_limit:
                raise UserError(_('La venta supera el crédito disponible. Registra un pago o revisa la autorización del cliente.'))

    def _prepare_invoice_vals(self):
        self._check_pos_deferred_payments()
        values = super()._prepare_invoice_vals()
        if any(payment.amount > 0 and payment.payment_method_id.type == 'pay_later' for payment in self.payment_ids):
            values['pos_financing_flow'] = 'ppd_partial' if self.pos_payment_flow == 'ppd_partial' else 'credit'
            term = self.config_id.ppd_payment_term_id or self.partner_id.property_payment_term_id or self.env.ref(
                'entintados_pdv.payment_term_pos_ppd')
            values['invoice_payment_term_id'] = term.id
            if 'l10n_mx_edi_payment_policy' in self.env['account.move']._fields:
                values['l10n_mx_edi_payment_policy'] = 'PPD'
        return values

    def _get_invoice_post_context(self):
        context = super()._get_invoice_post_context()
        if self.pos_payment_flow == 'ppd_partial':
            context = dict(context, bypass_risk=True)
        return context


class AccountMove(models.Model):
    _inherit = 'account.move'

    pos_financing_flow = fields.Selection(PAYMENT_FLOWS, default='standard', copy=False, readonly=True)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _pos_credit_exposure(self):
        """Keep a debt floor even when optional OCA accounting categories are off."""
        self.ensure_one()
        total, = self.env['account.move.line'].sudo()._read_group([
            ('partner_id.commercial_partner_id', '=', self.commercial_partner_id.id),
            ('company_id', '=', self.env.company.id), ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', 'asset_receivable'), ('reconciled', '=', False),
        ], [], ['amount_residual:sum'])[0]
        ledger = self.env.company.currency_id._convert(
            total or 0, self.risk_currency_id, self.env.company, fields.Date.context_today(self))
        return max(self.risk_gross_total, ledger, 0)
