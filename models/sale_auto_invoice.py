from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = 'res.company'

    sale_cash_payment_journal_id = fields.Many2one('account.journal',
        string='Diario de efectivo de Ventas', check_company=True,
        domain="[('type', '=', 'cash'), ('company_id', '=', id)]")


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sale_cash_payment_journal_id = fields.Many2one(
        related='company_id.sale_cash_payment_journal_id', readonly=False)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    can_prepare_reinvoice = fields.Boolean(compute='_compute_can_prepare_reinvoice')

    def _normal_customer_invoices(self):
        return self.invoice_ids.filtered(lambda invoice:
            invoice.move_type == 'out_invoice' and not invoice.is_advance_invoice)

    def _has_only_cancelled_customer_invoices(self):
        invoices = self._normal_customer_invoices()
        return bool(invoices) and all(
            invoice.state == 'cancel'
            and invoice.l10n_mx_edi_cfdi_state not in ('sent', 'global_sent', 'cancel_requested')
            for invoice in invoices
        )

    @api.depends('state', 'invoice_status', 'invoice_ids.state', 'invoice_ids.l10n_mx_edi_cfdi_state',
                 'invoice_ids.is_advance_invoice', 'invoice_ids.move_type')
    def _compute_can_prepare_reinvoice(self):
        for order in self:
            order.can_prepare_reinvoice = (order.state == 'sale' and order.invoice_status == 'to invoice'
                                           and order._has_only_cancelled_customer_invoices())

    def action_prepare_reinvoice(self):
        self.ensure_one()
        self.check_access('write')
        self.env['res.company']._with_locked_records(self)
        self.invalidate_recordset(['state', 'invoice_ids', 'invoice_status', 'can_prepare_reinvoice'])
        invoices = self._normal_customer_invoices()
        self.env['res.company']._with_locked_records(invoices)
        invoices.invalidate_recordset(['state', 'l10n_mx_edi_cfdi_state'])
        if not self.can_prepare_reinvoice:
            raise UserError(_('La orden debe estar confirmada, tener líneas facturables y tener canceladas sus facturas anteriores.'))
        self._check_sale_payment_policy()
        drafts = self.with_context(sale_auto_invoice_checkout=True)._create_invoices(final=True)
        return self.action_view_invoice(invoices=drafts)

    def _sale_cash_journal(self, invoice):
        journal = invoice.company_id.sale_cash_payment_journal_id
        domain = [('type', '=', 'cash'), ('company_id', '=', invoice.company_id.id),
                  ('active', '=', True), '|', ('currency_id', '=', False),
                  ('currency_id', '=', invoice.currency_id.id)]
        if not journal:
            journals = invoice.env['account.journal'].search(domain, limit=2)
            if len(journals) != 1:
                raise UserError(_('Configura el Diario de efectivo de Ventas en los ajustes de esta compañía.'))
            journal = journals
        if (journal.type != 'cash' or not journal.active or journal.company_id != invoice.company_id
                or (journal.currency_id and journal.currency_id != invoice.currency_id)
                or not journal.default_account_id):
            raise UserError(_('Revisa la compañía, moneda y cuenta de caja del Diario de efectivo de Ventas.'))
        if not journal.inbound_payment_method_line_ids.filtered(lambda method: method.code == 'manual'):
            raise UserError(_('Configura un método de cobro manual en el Diario de efectivo de Ventas.'))
        return journal

    def _sale_pay_invoice_cash(self, invoice):
        if invoice.state != 'posted' or invoice.currency_id.is_zero(invoice.amount_residual):
            return
        journal = self._sale_cash_journal(invoice)
        lines = invoice.line_ids.filtered(lambda line: line.account_type == 'asset_receivable' and not line.reconciled)
        if len(lines.account_id) != 1 or invoice.amount_residual < 0:
            raise UserError(_('No se puede registrar automáticamente el cobro de esta factura.'))
        method = journal.inbound_payment_method_line_ids.filtered(lambda method: method.code == 'manual')[:1]
        payment = invoice.env['account.payment'].with_company(invoice.company_id).create({
            'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': invoice.commercial_partner_id.id,
            'company_id': invoice.company_id.id, 'currency_id': invoice.currency_id.id,
            'amount': invoice.amount_residual, 'date': fields.Date.context_today(invoice),
            'journal_id': journal.id, 'payment_method_line_id': method.id,
            'destination_account_id': lines.account_id.id,
            'force_outstanding_account_id': journal.default_account_id.id,
            'memo': _('Cobro en efectivo de Ventas: %s', invoice.name),
        })
        payment.action_post()
        counterpart = payment.move_id.line_ids.filtered(lambda line:
            line.account_id == lines.account_id and not line.reconciled)
        (lines | counterpart).reconcile()
        if not invoice.currency_id.is_zero(invoice.amount_residual):
            raise UserError(_('No se pudo liquidar la factura con el pago en efectivo.'))

    def action_generate_validated_invoice(self, deduct_down_payments=True):
        self = self.with_context(sale_auto_invoice_checkout=True)
        self.check_access('write')
        if not self or any(order.state != 'sale' for order in self):
            raise UserError(_('Confirma la orden de venta antes de generar su factura normal.'))
        self._check_sale_payment_policy()
        with self.env.cr.savepoint():
            self.env['res.company']._with_locked_records(self)
            self.invalidate_recordset(['invoice_status', 'invoice_ids'])
            invoiceable = self.filtered(lambda order: order.invoice_status == 'to invoice')
            invoices = (invoiceable.with_context(sale_auto_invoice_checkout=True)._create_invoices(final=bool(deduct_down_payments))
                        if invoiceable else self.env['account.move'])
            if not invoices:
                invoices = self.invoice_ids.filtered(lambda move:
                    move.move_type == 'out_invoice' and move.state != 'cancel' and not move.is_advance_invoice)
            if not invoices:
                raise UserError(_('No hay líneas facturables para generar una factura normal.'))
            self.env['res.company']._with_locked_records(invoices)
            invoices.invalidate_recordset(['state', 'amount_residual', 'payment_state'])
            for invoice in invoices:
                if invoice.move_type != 'out_invoice' or invoice.is_advance_invoice:
                    raise UserError(_('Esta operación genera únicamente facturas normales de cliente.'))
                policies = set(invoice.invoice_line_ids.sale_line_ids.order_id.mapped('l10n_mx_edi_payment_policy')) - {False}
                if len(policies) != 1:
                    raise UserError(_('La factura debe tener una sola política de pago de Ventas: PUE o PPD.'))
                policy = policies.pop()
                if invoice.state == 'draft':
                    invoice.l10n_mx_edi_payment_policy = policy
                if policy == 'PUE' and invoice.amount_residual:
                    self._sale_cash_journal(invoice)
                    if invoice.state == 'draft':
                        invoice.l10n_mx_edi_payment_method_id = self.env.ref('l10n_mx_edi.payment_method_efectivo')
                if invoice.state == 'draft':
                    result = invoice.action_post()
                    if invoice.state != 'posted':
                        # Keep native credit/risk approval actions and their draft invoice.
                        if isinstance(result, dict):
                            return result
                        raise UserError(_('La factura requiere completar su validación antes de continuar.'))
                if policy == 'PUE':
                    self._sale_pay_invoice_cash(invoice)
            return self.action_view_invoice(invoices=invoices)
