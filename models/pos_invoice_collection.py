import logging

from odoo import api, fields, models, _, SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.modules.registry import Registry

_logger = logging.getLogger(__name__)


class PosInvoiceCollection(models.Model):
    _inherit = 'pos.order'

    invoice_collection_processed = fields.Boolean(copy=False, readonly=True)
    invoice_collection_payment_ids = fields.One2many(
        'account.payment', 'pos_invoice_collection_order_id', string='Abonos a facturas', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([dict(vals, invoice_collection_processed=False) for vals in vals_list])

    def write(self, vals):
        if not self.env.su and 'invoice_collection_processed' in vals:
            vals = {key: value for key, value in vals.items() if key != 'invoice_collection_processed'}
        return super().write(vals)

    def _invoice_collection_lines(self):
        self.ensure_one()
        return self.lines.filtered(lambda line: line.settled_invoice_id or line.settled_order_id.account_move)

    def _process_saved_order(self, draft):
        self.ensure_one()
        if self._invoice_collection_lines():
            self.write({'to_invoice': False, 'pos_payment_flow': 'standard'})
        return super()._process_saved_order(draft)

    def _prepare_invoice_vals(self):
        if self._invoice_collection_lines():
            raise UserError(_('El cobro de una factura existente genera un pago, no otra factura.'))
        return super()._prepare_invoice_vals()

    def action_view_invoice_collection_payments(self):
        self.ensure_one()
        return {
            'name': _('Abonos a facturas'), 'type': 'ir.actions.act_window',
            'res_model': 'account.payment', 'view_mode': 'list,form',
            'domain': [('pos_invoice_collection_order_id', '=', self.id)],
            'context': {'create': False},
        }

    def action_pos_order_paid(self):
        for order in self:
            if order._invoice_collection_lines():
                order.to_invoice = False
                order.pos_payment_flow = 'standard'
                order._collection_targets()  # Validate before confirming the ticket.
        result = super().action_pos_order_paid()
        for order in self:
            if order._invoice_collection_lines():
                order._register_invoice_collection()
        return result

    @api.depends('company_id', 'lines.settled_invoice_id', 'lines.settled_order_id.account_move')
    def _compute_l10n_mx_edi_is_cfdi_needed(self):
        super()._compute_l10n_mx_edi_is_cfdi_needed()
        for order in self:
            if order._invoice_collection_lines():
                order.l10n_mx_edi_is_cfdi_needed = False

    def _collection_targets(self):
        self.ensure_one()
        lines = self._invoice_collection_lines()
        if len(lines) != len(self.lines) or not self.partner_id:
            raise UserError(_('Cobra las facturas en un ticket separado de productos, anticipos u otras deudas, con el cliente identificado.'))
        targets = {}
        for line in lines:
            expected_product = self.config_id.settle_invoice_product_id if line.settled_invoice_id else self.config_id.settle_due_product_id
            if line.product_id != expected_product:
                raise UserError(_('Utiliza la opción de cobro de facturas pendientes para registrar el abono.'))
            invoices = line.settled_invoice_id or (line.settled_order_id.account_move | line.settled_order_id.split_invoice_ids)
            if len(invoices) != 1:
                raise UserError(_('Para abonar una venta dividida, selecciona cada factura desde la lista de facturas pendientes.'))
            invoice = invoices.sudo().with_company(self.company_id)
            if (invoice.state != 'posted' or invoice.move_type != 'out_invoice'
                    or invoice.l10n_mx_edi_cfdi_state in ('cancel', 'cancel_requested')
                    or invoice.company_id != self.company_id
                    or invoice.commercial_partner_id != self.partner_id.commercial_partner_id
                    or invoice.currency_id != self.currency_id):
                raise UserError(_('La factura debe estar registrada y corresponder al cliente, sucursal y moneda del cobro.'))
            if line.qty not in (0, 1) or line.discount or line.tax_ids:
                raise UserError(_('Las líneas de abono no admiten impuestos, descuentos ni cantidades distintas de uno.'))
            # Native pos_settle_due sets qty=0 and adds a negative pay_later line.
            amount = line.price_unit
            if amount < 0:
                raise UserError(_('El importe del abono no puede ser negativo.'))
            targets[invoice] = targets.get(invoice, 0.0) + amount
        return targets

    def _register_invoice_collection(self):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM pos_order WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset(['invoice_collection_processed'])
        if self.invoice_collection_processed:
            return
        targets = self._collection_targets()
        invoices = self.env['account.move'].sudo().browse([invoice.id for invoice in targets])
        self.env.cr.execute('SELECT id FROM account_move WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(invoices.ids)])
        invoices.line_ids.invalidate_recordset(['amount_residual', 'amount_residual_currency'])
        invoices.invalidate_recordset(['amount_residual'])
        for invoice, amount in targets.items():
            if self.currency_id.compare_amounts(amount, invoice.amount_residual) > 0:
                raise UserError(_('El abono supera el saldo pendiente de la factura %s. Actualiza sus datos e intenta nuevamente.', invoice.name))
        deferred = self.payment_ids.filtered(lambda payment: payment.payment_method_id.type == 'pay_later')
        real = self.payment_ids - deferred
        if any(payment.amount > 0 for payment in deferred):
            raise UserError(_('El cobro de una factura requiere dinero recibido; no puede volver a financiarse.'))
        received = sum(real.mapped('amount'))
        if received <= 0 or self.currency_id.compare_amounts(received, sum(targets.values())):
            raise UserError(_('El dinero recibido, descontando el cambio, debe coincidir con el abono seleccionado.'))
        if any(payment.amount < 0 and not payment.is_change for payment in real):
            raise UserError(_('Los abonos no admiten devoluciones de pagos.'))
        interim = self.company_id.account_default_pos_receivable_account_id
        if not interim or not interim.reconcile:
            raise UserError(_('Configura una cuenta puente conciliable para el Punto de Venta.'))
        amounts = {payment: payment.amount for payment in real if not payment.is_change}
        for change in real.filtered('is_change'):
            cash = next((payment for payment in amounts if payment.payment_method_id == change.payment_method_id), None)
            if not cash:
                raise UserError(_('El cambio debe corresponder a un pago en efectivo del mismo ticket.'))
            amounts[cash] += change.amount
        if any(amount < 0 for amount in amounts.values()):
            raise UserError(_('El cambio no puede superar el efectivo recibido.'))
        remaining = dict(targets)
        payments = self.env['account.payment'].sudo().with_company(self.company_id)
        for pos_payment, amount in amounts.items():
            if self.currency_id.is_zero(amount):
                continue
            method = pos_payment.payment_method_id
            journal = method.sudo().journal_id
            method_line = journal.inbound_payment_method_line_ids.filtered(lambda item: item.code == 'manual')[:1]
            if not method_line or journal.company_id not in self.company_id.parent_ids:
                raise UserError(_('Configura un método de pago entrante manual en el diario de %s.', method.name))
            receivable_accounts = invoices.line_ids.filtered(lambda line: line.account_type == 'asset_receivable').account_id
            if interim in receivable_accounts:
                raise UserError(_('La cuenta puente del PdV debe ser distinta de la cuenta por cobrar del cliente.'))
            pending = amount
            for invoice in remaining:
                part = min(pending, remaining[invoice])
                if self.currency_id.is_zero(part):
                    continue
                invoice_lines = invoice.line_ids.filtered(lambda line: line.account_type == 'asset_receivable' and not line.reconciled)
                payment = payments.create({
                    'payment_type': 'inbound', 'partner_type': 'customer',
                    'partner_id': self.partner_id.commercial_partner_id.id,
                    'company_id': self.company_id.id, 'currency_id': self.currency_id.id,
                    'amount': part, 'date': fields.Date.context_today(self, pos_payment.payment_date),
                    'journal_id': journal.id, 'payment_method_line_id': method_line.id,
                    'pos_payment_method_id': method.id,
                    'force_outstanding_account_id': interim.id,
                    'destination_account_id': invoice_lines.account_id.id,
                    'memo': _('Abono desde %s', self.name),
                    'pos_invoice_collection_order_id': self.id,
                    'pos_invoice_collection_payment_id': pos_payment.id,
                })
                payment.action_post()
                if not pos_payment.account_move_id:
                    pos_payment.sudo().account_move_id = payment.move_id
                credit = payment.move_id.line_ids.filtered(lambda line: line.account_id in invoice_lines.account_id and line.balance < 0 and not line.reconciled)
                if not credit:
                    raise UserError(_('No fue posible conciliar el abono con la cuenta por cobrar de %s.', invoice.name))
                (invoice_lines | credit).with_context(bypass_risk=True).reconcile()
                pending -= part
                remaining[invoice] -= part
        if any(not self.currency_id.is_zero(amount) for amount in remaining.values()):
            raise UserError(_('El abono no pudo aplicarse completamente a las facturas seleccionadas.'))
        self.sudo().write({'invoice_collection_processed': True, 'state': 'done', 'to_invoice': False})
        for invoice in invoices:
            invoice.message_post(body=_('Abono registrado desde %s. Consulta los pagos relacionados y su complemento de pago.', self.name))
        dbname, invoice_ids, company_ids = self.env.cr.dbname, invoices.ids, self.company_id.ids

        def send_after_commit():
            with Registry(dbname).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {'allowed_company_ids': company_ids})
                try:
                    env['account.move'].browse(invoice_ids).l10n_mx_edi_cfdi_invoice_try_update_payments()
                    cr.commit()
                except Exception:
                    cr.rollback()
                    _logger.exception('No se pudo timbrar el complemento de los abonos a facturas %s; el cron volverá a intentarlo.', invoice_ids)
        self.env.cr.postcommit.add(send_after_commit)

class AccountPayment(models.Model):
    _inherit = 'account.payment'

    pos_invoice_collection_order_id = fields.Many2one('pos.order', copy=False, readonly=True, ondelete='restrict', index=True)
    pos_invoice_collection_payment_id = fields.Many2one('pos.payment', copy=False, readonly=True, ondelete='restrict', index=True)


class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.depends('pos_order_line_ids', 'amount_residual_signed', 'pos_order_line_ids.order_id.invoice_collection_processed',
                 'pos_order_line_ids.order_id.session_id.state', 'pos_order_line_ids.price_unit')
    def _compute_pos_amount_unsettled(self):
        super()._compute_pos_amount_unsettled()
        for invoice in self:
            already_registered = invoice.pos_order_line_ids.filtered(
                lambda line: line.order_id.invoice_collection_processed and line.order_id.session_id.state != 'closed')
            invoice.pos_amount_unsettled += sum(already_registered.mapped('price_unit'))


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _get_closed_orders(self):
        orders = super()._get_closed_orders()
        if self.env.context.get('exclude_registered_invoice_collections'):
            return orders.filtered(lambda order: not order.invoice_collection_processed)
        return orders

    def _accumulate_amounts(self, data):
        collections = self._get_closed_orders().filtered('invoice_collection_processed')
        data = super(PosSession, self.with_context(exclude_registered_invoice_collections=True))._accumulate_amounts(data)
        for payment in collections.payment_ids.filtered(lambda item: item.payment_method_id.type != 'pay_later'):
            method = payment.payment_method_id
            split = method.split_transactions
            key = payment if split else method
            kind = 'cash' if method.type == 'cash' else 'bank'
            bucket = data[f'{"split" if split else "combine"}_receivables_{kind}']
            bucket[key] = self._update_amounts(bucket[key], {'amount': payment.amount}, payment.payment_date)
            invoice_bucket = data[f'{"split" if split else "combine"}_invoice_receivables']
            invoice_bucket[key] = self._update_amounts(invoice_bucket[key], {'amount': payment.amount}, payment.payment_date)
            account_payments = payment.pos_order_id.invoice_collection_payment_ids.filtered(
                lambda item: item.pos_invoice_collection_payment_id == payment)
            if payment.is_change:
                account_payments = payment.pos_order_id.invoice_collection_payment_ids.filtered(
                    lambda item: item.pos_payment_method_id == method)
            lines = account_payments.move_id.line_ids.filtered(lambda line: line.account_id == self.company_id.account_default_pos_receivable_account_id)
            data[f'{"split" if split else "combine"}_inv_payment_receivable_lines'][key] |= lines
        return data

    def _reconcile_account_move_lines(self, data):
        data = super()._reconcile_account_move_lines(data)
        # Split cash payments and their change are separate POS rows but share
        # one net accounting payment. Reconcile their remaining bridge entries.
        for session in self:
            collections = session.order_ids.filtered('invoice_collection_processed')
            if not collections:
                continue
            account = session.company_id.account_default_pos_receivable_account_id
            lines = (collections.invoice_collection_payment_ids.move_id.line_ids
                     | session.move_id.line_ids).filtered(
                         lambda line: line.account_id == account and not line.reconciled)
            if lines:
                lines.sudo().reconcile()
        return data
