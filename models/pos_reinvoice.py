from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError


_REINVOICE = object()
_SOURCE_FIELDS = ('reinvoice_pos_source_id', 'reinvoice_sale_source_id')
_REOPEN_HISTORY = ('reopened_move_ids', 'reopened_picking_ids')


class PosOrder(models.Model):
    _inherit = 'pos.order'

    reinvoice_pos_source_id = fields.Many2one('pos.order', readonly=True, copy=False, ondelete='restrict')
    reinvoice_sale_source_id = fields.Many2one('sale.order', readonly=True, copy=False, ondelete='restrict')
    reinvoice_revision = fields.Integer(readonly=True, copy=False)
    reopened_move_ids = fields.Many2many('account.move', 'pos_reopened_account_move_rel',
                                        'order_id', 'move_id', readonly=True, copy=False)
    reopened_picking_ids = fields.Many2many('stock.picking', 'pos_reopened_stock_picking_rel',
                                           'order_id', 'picking_id', readonly=True, copy=False)

    _reinvoice_pos_source_unique = models.Constraint(
        'UNIQUE(reinvoice_pos_source_id)', 'Esta orden de PdV ya tiene un ticket de reemplazo.')
    _reinvoice_sale_source_unique = models.Constraint(
        'UNIQUE(reinvoice_sale_source_id)', 'Esta orden de Venta ya tiene un ticket de reemplazo.')

    @api.model
    def _load_pos_data_fields(self, config):
        return [field for field in super()._load_pos_data_fields(config)
                if field not in (*_SOURCE_FIELDS, *_REOPEN_HISTORY)]

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('_reinvoice_operation') is not _REINVOICE:
            if any(any(values.get(field) for field in (*_SOURCE_FIELDS, *_REOPEN_HISTORY, 'reinvoice_revision'))
                   for values in vals_list):
                raise AccessError(_('Usa el flujo de refacturación para vincular un ticket de reemplazo.'))
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get('_reinvoice_operation') is not _REINVOICE:
            if any(field in vals for field in _REOPEN_HISTORY) or (
                    'reinvoice_revision' in vals
                    and any(order.reinvoice_revision != (vals['reinvoice_revision'] or 0) for order in self)):
                raise AccessError(_('El historial de reapertura es de solo lectura.'))
        for field in _SOURCE_FIELDS:
            if field in vals and any(order[field].id != (vals[field] or False) for order in self):
                raise AccessError(_('El origen de una refacturación es de solo lectura.'))
        return super().write(vals)

    @api.model
    def _process_order(self, order, existing_order):
        if existing_order and existing_order.reinvoice_revision:
            self.env['res.company']._with_locked_records(existing_order)
            existing_order.invalidate_recordset(['reinvoice_revision'])
            if order.get('reinvoice_revision') != existing_order.reinvoice_revision:
                raise UserError(_('Este ticket se reabrió. Recarga el Punto de Venta antes de guardar o cobrar.'))
        return super()._process_order(order, existing_order)

    @api.model
    def reopen_original_pos_for_reinvoice(self, source_id, session_id, cash_returned=False):
        if (not self.env.user.has_group('point_of_sale.group_pos_manager')
                or not self.env.user.has_group('account.group_account_invoice')):
            raise AccessError(_('La reapertura requiere permisos de administración de PdV y facturación.'))
        source = self.browse(source_id).exists()
        source.check_access('write')
        lock = self.env['res.company']._with_locked_records
        lock(source)
        source.invalidate_recordset()
        session = source.session_id
        lock(session)
        session.invalidate_recordset(['state', 'move_id'])
        if not source or session.id != session_id or session.state != 'opened' or session.move_id:
            raise UserError(_('Solo se pueden reabrir tickets de la misma sesión de caja abierta y sin asiento de cierre.'))
        if source.state == 'draft' and source.reinvoice_revision:
            return source.uuid
        refunds = source.lines.refund_orderline_ids.order_id
        if (source.state not in ('paid', 'done') or source.is_refund or source.split_invoice_ids
                or source.lines.sale_order_origin_id or source.lines.sale_order_line_id or source.shipping_date
                or any(line.is_reward_line or line.coupon_id or line._is_accounting_advance_line() for line in source.lines)
                or refunds.filtered(lambda order: order.state not in ('draft', 'cancel'))
                or self.sudo().search_count([('reinvoice_pos_source_id', '=', source.id)], limit=1)):
            raise UserError(_('El ticket tiene operaciones vinculadas que requieren revisión antes de reabrirlo.'))
        invoice = source.account_move
        payments = source.payment_ids
        payment_moves = payments.account_move_id
        moves = invoice | payment_moves
        moves.check_access('write')
        lock(moves)
        moves.invalidate_recordset()
        if (cash_returned is not True or not invoice or invoice.state != 'posted'
                or invoice.move_type != 'out_invoice' or invoice.payment_state != 'paid'
                or not payments or source.amount_total <= 0
                or source.currency_id.compare_amounts(sum(payments.mapped('amount')), source.amount_total)
                or any(payment.is_change or payment.amount <= 0 or payment.payment_method_id.type != 'cash'
                       or payment.account_move_id.state != 'posted' for payment in payments)
                or len(payment_moves) != len(payments)):
            raise UserError(_('La reapertura automática requiere una factura pagada y cobros íntegros en efectivo, sin cambio ni pagos compartidos.'))
        if (source.l10n_mx_edi_cfdi_state in ('sent', 'global_sent', 'cancel_requested')
                or any(move.l10n_mx_edi_cfdi_state in ('sent', 'global_sent', 'cancel_requested')
                       or move._need_cancel_request() for move in moves)
                or (invoice.pos_order_ids - source) or invoice.reversal_move_ids.filtered(lambda move: move.state != 'cancel')
                or payment_moves.pos_payment_ids - payments):
            raise UserError(_('El ticket tiene documentos fiscales, devoluciones o pagos compartidos que deben resolverse antes de reabrirlo.'))
        lines = moves.line_ids
        reconciliations = lines.matched_debit_ids | lines.matched_credit_ids
        original_matches = reconciliations.filtered(lambda match:
            match.debit_move_id.move_id in moves and match.credit_move_id.move_id in moves)
        # Unlinking these reconciliations reverses their cash-basis entries
        # through Odoo's normal accounting flow.
        cash_basis = self.env['account.move'].search([
            ('tax_cash_basis_rec_id', 'in', original_matches.ids),
            ('tax_cash_basis_origin_move_id', '=', invoice.id),
            ('company_id', '=', source.company_id.id),
        ])
        checked_lines = (moves | cash_basis).line_ids
        peers = checked_lines.matched_debit_ids.debit_move_id | checked_lines.matched_credit_ids.credit_move_id
        if peers.move_id - (moves | cash_basis):
            raise UserError(_('Hay conciliaciones con otros documentos. Revísalas antes de reabrir el ticket.'))
        for refund in refunds.filtered(lambda order: order.state == 'draft'):
            self.cancel_reinvoice_refund(refund.uuid, session.id)
        self.with_context(skip_stock_return_credit=True)._return_reinvoice_stock(source, automatic=True)
        pickings = source.picking_ids
        pickings.filtered(lambda picking: picking.state not in ('done', 'cancel')).action_cancel()
        returns = pickings.move_ids.returned_move_ids.picking_id
        source.with_context(_reinvoice_operation=_REINVOICE).write({
            'reopened_move_ids': [Command.link(move.id) for move in moves | cash_basis],
            'reopened_picking_ids': [Command.link(picking.id) for picking in pickings | returns],
            'reinvoice_revision': source.reinvoice_revision + 1,
            'account_move': False,
        })
        invoice.invalidate_recordset(['pos_order_ids'])
        invoice.button_cancel()
        payment_moves.filtered(lambda move: move.state != 'cancel').button_cancel()
        if moves.filtered(lambda move: move.state != 'cancel'):
            raise UserError(_('No se pudieron cancelar todos los documentos originales.'))
        reversals = cash_basis.exists().reversal_move_ids
        if reversals:
            source.with_context(_reinvoice_operation=_REINVOICE).write({
                'reopened_move_ids': [Command.link(move.id) for move in reversals],
            })
        pickings.write({'pos_order_id': False})
        payments.unlink()
        # The native write guard protects paid tickets. Only reset the state
        # here, under the order lock, after every original movement is reversed.
        source.flush_recordset(['state'])
        self.env.cr.execute("UPDATE pos_order SET state = 'draft' WHERE id = %s", [source.id])
        source.invalidate_recordset(['state'])
        source.modified(['state'])
        source.write({'amount_paid': 0, 'amount_return': 0, 'to_invoice': True})
        session.config_id.notify_synchronisation(session.id, 0)
        return source.uuid

    @api.model
    def read_refund_origin_for_payment(self, uuid):
        refund = self.search([('uuid', '=', uuid), ('state', '=', 'draft'), ('is_refund', '=', True)], limit=1)
        origin = refund.refunded_order_id
        if not origin:
            return {}
        origin.check_access('read')
        return origin.read_pos_data([], refund.config_id)

    @api.model
    def cancel_reinvoice_refund(self, uuid, session_id):
        if (not self.env.user.has_group('point_of_sale.group_pos_manager')
                or not self.env.user.has_group('account.group_account_invoice')):
            raise AccessError(_('La refacturación requiere permisos de administración de PdV y facturación.'))
        refund = self.search([('uuid', '=', uuid), ('session_id', '=', session_id), ('is_refund', '=', True)], limit=1)
        if not refund or refund.session_id.state != 'opened':
            raise UserError(_('La devolución debe pertenecer a la sesión abierta.'))
        source = refund.refunded_order_id
        self.env['res.company']._with_locked_records(source | refund)
        refund.invalidate_recordset()
        if refund.state == 'cancel':
            return True
        refund.check_access('write')
        if (refund.state != 'draft' or not source or refund.payment_ids or refund.account_move
                or refund.split_invoice_ids or refund.picking_ids or refund.l10n_mx_edi_cfdi_state):
            raise UserError(_('Solo se puede deshacer un borrador sin pagos, facturas ni movimientos de almacén.'))
        refund.with_context(active_ids=[]).action_pos_order_cancel()
        return True

    @api.model
    def reopen_original_sale_for_reinvoice(self, source_id, session_id, cash_returned=False):
        if (not self.env.user.has_group('point_of_sale.group_pos_manager')
                or not self.env.user.has_group('account.group_account_invoice')):
            raise AccessError(_('La refacturación requiere permisos de administración de PdV y facturación.'))
        source = self.env['sale.order'].browse(source_id).exists()
        session = self.env['pos.session'].browse(session_id).exists()
        source.check_access('write')
        session.check_access('read')
        if not source or not session or session.state != 'opened' or source.company_id != session.company_id:
            raise UserError(_('Selecciona una orden de la misma compañía que la sesión abierta.'))
        lock = self.env['res.company']._with_locked_records
        lock(source)
        source.invalidate_recordset()
        if self.sudo().search_count([('reinvoice_sale_source_id', '=', source.id)], limit=1):
            raise UserError(_('Esta orden ya tiene un reemplazo registrado. Revisa esa refacturación antes de reabrirla.'))
        if source.pos_order_line_ids.order_id.filtered(lambda order: order.state not in ('draft', 'cancel')):
            raise UserError(_('Esta orden tiene operaciones registradas en PdV. Revisa sus movimientos antes de reabrirla.'))
        invoices = source._normal_customer_invoices()
        invoices.check_access('read')
        lock(invoices)
        invoices.invalidate_recordset()
        active = invoices.filtered(lambda invoice: invoice.state != 'cancel')
        if source.state == 'draft' and invoices and not active:
            return {'sale_id': source.id}
        if source.state != 'sale' or source.locked or not active:
            raise UserError(_('Selecciona una orden confirmada, desbloqueada y con una factura vigente.'))
        if any(invoice.l10n_mx_edi_cfdi_state in ('sent', 'global_sent', 'cancel_requested')
               or invoice._need_cancel_request() for invoice in active):
            raise UserError(_('La factura requiere su proceso de cancelación fiscal antes de reabrir la orden.'))
        if (source.invoice_ids - invoices).filtered(lambda invoice: invoice.state != 'cancel'):
            raise UserError(_('La orden tiene anticipos o notas de crédito vigentes. Revisa su cancelación en Contabilidad.'))
        if (active.invoice_line_ids.sale_line_ids.order_id - source or active.pos_order_ids
                or any(invoice.state != 'posted' or invoice.payment_state != 'paid'
                       or invoice.currency_id != source.currency_id for invoice in active)):
            raise UserError(_('La orden debe tener facturas propias y totalmente pagadas, en la moneda de la orden.'))
        payments = active._get_reconciled_payments()
        payments.check_access('write')
        lock(payments)
        payments.invalidate_recordset()
        if (cash_returned is not True or not payments
                or source.currency_id.compare_amounts(sum(payments.mapped('amount')), sum(active.mapped('amount_total')))
                or any(payment.payment_type != 'inbound' or payment.journal_id.type != 'cash'
                       or payment.currency_id != source.currency_id or payment.state not in ('paid', 'in_process')
                       or payment.reconciled_statement_line_ids
                       or (payment.reconciled_invoice_ids | payment.reconciled_bill_ids) - active
                       or payment.move_id._need_cancel_request() for payment in payments)):
            raise UserError(_('Solo se pueden cancelar automáticamente cobros en efectivo exclusivos de la orden, sin conciliación bancaria. Confirma primero la devolución del efectivo.'))
        active.check_access('write')
        self.with_context(skip_stock_return_credit=True)._return_reinvoice_stock(source, automatic=True)
        active.button_cancel()
        payments.action_cancel()
        if any(invoice.state != 'cancel' for invoice in active) or any(payment.state != 'canceled' for payment in payments):
            raise UserError(_('Odoo no pudo cancelar todos los documentos originales.'))
        source.action_cancel()
        source.action_draft()
        return {'sale_id': source.id}

    @api.model
    def prepare_reinvoice_ticket(self, model, source_id, session_id, sale_refund_confirmed=False, guided=False,
                                 cash_refund_confirmed=False):
        if (not self.env.user.has_group('point_of_sale.group_pos_manager')
                or not self.env.user.has_group('account.group_account_invoice')):
            raise AccessError(_('La refacturación requiere permisos de administración de PdV y facturación.'))
        if model not in ('pos.order', 'sale.order'):
            raise UserError(_('Selecciona una orden de PdV o de Ventas.'))
        source = self.env[model].browse(source_id).exists()
        session = self.env['pos.session'].browse(session_id).exists()
        source.check_access('read')
        session.check_access('read')
        if (not source or not session or session.state != 'opened'
                or source.company_id != session.company_id
                or source.currency_id != session.config_id.currency_id):
            raise UserError(_('Usa una sesión abierta con la misma compañía y moneda que la orden original.'))
        self.env['res.company']._with_locked_records(source)
        source.invalidate_recordset()
        is_pos = model == 'pos.order'
        field = 'reinvoice_pos_source_id' if is_pos else 'reinvoice_sale_source_id'
        existing = self.search([(field, '=', source.id)], limit=1)
        if existing:
            if existing.state != 'draft' or existing.session_id != session:
                raise UserError(_('Esta orden ya tiene un reemplazo: %s.', existing.display_name))
            return existing.uuid
        invoices = (source.account_move | source.split_invoice_ids) if is_pos else source._normal_customer_invoices()
        invoices.check_access('read')
        self.env['res.company']._with_locked_records(invoices)
        invoices.invalidate_recordset(['state', 'payment_state', 'amount_residual', 'l10n_mx_edi_cfdi_state'])
        active_invoices = invoices.filtered(lambda invoice: invoice.state != 'cancel')
        canceled_collection = (is_pos and bool(invoices)
                               and all(invoice.move_type == 'out_invoice' and not invoice.is_advance_invoice for invoice in invoices)
                               and source._reinvoice_collection_is_canceled())
        refunds = source.lines.refund_orderline_ids.order_id if is_pos else self.browse()
        posted_refunds = refunds.filtered(lambda order: order.state in ('paid', 'done'))
        credits = posted_refunds.account_move.filtered(lambda move: move.state == 'posted' and move.move_type == 'out_refund')
        offset = (len(active_invoices) == 1 and credits
                  and all(credit.reversed_entry_id == active_invoices for credit in credits)
                  and source.currency_id.compare_amounts(sum(credits.mapped('amount_total')), active_invoices.amount_total) == 0)
        if not canceled_collection and (not active_invoices or any(
                invoice.move_type != 'out_invoice' or invoice.is_advance_invoice or invoice.state != 'posted'
                or (invoice.payment_state != 'paid' and not (offset and invoice.payment_state == 'reversed'))
                or not invoice.currency_id.is_zero(invoice.amount_residual)
                or invoice.amount_total <= 0 for invoice in active_invoices)):
            raise UserError(_('Selecciona una orden con sus facturas vigentes pagadas al 100 %.'))
        fiscal = invoices.filtered(lambda invoice: invoice.l10n_mx_edi_cfdi_state in ('sent', 'global_sent', 'cancel_requested')
                                   or (invoice.state != 'cancel' and invoice._need_cancel_request()))
        if fiscal:
            if guided:
                return {'invoice_id': fiscal[0].id}
            raise UserError(_('La factura tiene una cancelación fiscal pendiente o requiere el flujo de cancelación del CFDI.'))
        if is_pos:
            shared = self.sudo().search_count([
                ('id', '!=', source.id), '|', ('account_move', 'in', active_invoices.ids),
                ('split_invoice_ids', 'in', active_invoices.ids),
            ], limit=1)
        else:
            shared = (active_invoices.invoice_line_ids.sale_line_ids.order_id - source
                      or active_invoices.pos_order_ids)
        if shared:
            raise UserError(_('La factura incluye otras órdenes. Gestiona su cancelación desde Contabilidad.'))
        active_invoices.check_access('write')
        if is_pos:
            if (source.is_refund or source.amount_total <= 0 or source.state not in ('paid', 'done', 'cancel')
                    or source.l10n_mx_edi_cfdi_state in ('sent', 'global_sent', 'cancel_requested')):
                raise UserError(_('Selecciona una venta de PdV y completa su cancelación fiscal si aplica.'))
            if source.picking_ids.filtered(lambda picking: picking.state not in ('cancel', 'done')):
                raise UserError(_('Cancela o resuelve las entregas pendientes del ticket original.'))
            rows = self._reinvoice_pos_lines(source)
            if canceled_collection:
                pending_refunds = refunds.filtered(lambda order: order.state == 'draft')
                if posted_refunds or pending_refunds.filtered(lambda order: order.payment_ids):
                    raise UserError(_('Ya existe una devolución con pagos para esta orden. Revisa el reembolso antes de cobrar nuevamente.'))
                for pending in pending_refunds:
                    self.cancel_reinvoice_refund(pending.uuid, session.id)
            elif guided and not posted_refunds:
                refund = self._complete_reinvoice_cash_refund(source, session, refunds, active_invoices,
                                                              cash_refund_confirmed)
                posted_refunds = refund
                offset = True
            else:
                self._check_reinvoice_pos_refunds(source)
                if posted_refunds and not offset:
                    raise UserError(_('Verifica que la devolución tenga una nota de crédito registrada por el total de la factura original.'))
        else:
            if source.state not in ('sale', 'cancel'):
                raise UserError(_('Selecciona una orden de Venta confirmada o cancelada.'))
            if source.pos_order_line_ids.order_id.filtered(lambda order: not order.is_refund and order.state in ('paid', 'done')):
                raise UserError(_('Esta orden se cobró desde PdV. Recupera el ticket original desde la opción Punto de Venta.'))
            if not sale_refund_confirmed:
                raise UserError(_('Confirma que se registró el reembolso del cobro original, si lo hubo.'))
            rows = self._reinvoice_sale_lines(source)
        if not rows:
            raise UserError(_('La orden no contiene productos para recuperar.'))
        self._return_reinvoice_stock(source, automatic=not is_pos or canceled_collection)
        if not offset and active_invoices:
            active_invoices.button_cancel()
            if active_invoices.filtered(lambda invoice: invoice.state != 'cancel'):
                raise UserError(_('Odoo no pudo cancelar todas las facturas de la orden.'))
        if not is_pos and source.state == 'sale':
            source.check_access('write')
            source.action_cancel()
        reference, tracking = session.config_id._get_next_order_refs()
        ticket = self.with_context(_reinvoice_operation=_REINVOICE).create({
            field: source.id,
            'session_id': session.id, 'partner_id': source.partner_id.id,
            'pricelist_id': source.pricelist_id.id, 'fiscal_position_id': source.fiscal_position_id.id,
            'pos_reference': reference, 'tracking_number': tracking,
            'amount_tax': 0, 'amount_total': 0, 'amount_paid': 0, 'amount_return': 0,
            'to_invoice': True,
        })
        linked = {}
        for key, parent, vals in rows:
            line = self.env['pos.order.line'].create(dict(
                vals, order_id=ticket.id, combo_parent_id=linked[parent].id if parent else False,
                price_subtotal=0, price_subtotal_incl=0,
            ))
            linked[key] = line
            line._onchange_amount_line_all()
        ticket._compute_prices()
        return ticket.uuid

    def _reinvoice_collection_is_canceled(self):
        self.ensure_one()
        payments = self.payment_ids.filtered(lambda payment: payment.amount and not payment.is_change)
        return bool(payments) and all(
            payment.payment_method_id.type != 'pay_later' and payment.account_move_id.state == 'cancel'
            for payment in payments
        )

    def _prepare_guided_reinvoice_refund(self, source, session, refunds, invoices):
        if (len(invoices) != 1 or invoices != source.account_move
                or source.config_id.current_session_id != session
                or any(payment.amount and payment.payment_method_id.type == 'pay_later' for payment in source.payment_ids)
                or source.currency_id.compare_amounts(sum(source.payment_ids.mapped('amount')), source.amount_total)):
            raise UserError(_('La devolución en caja requiere una sola factura y un cobro completo registrado en este PdV.'))
        drafts = refunds.filtered(lambda order: order.state == 'draft')
        if drafts:
            if len(drafts) != 1 or drafts.session_id != session or drafts.lines.filtered(
                    lambda line: line.refunded_orderline_id.order_id != source):
                raise UserError(_('Revisa la devolución que ya está en curso para esta orden.'))
            return drafts
        refund = source._refund()
        refund.to_invoice = True
        copies = {line.refunded_orderline_id.id: line for line in refund.lines}
        for original in source.lines:
            if original.combo_parent_id and original.id in copies and original.combo_parent_id.id in copies:
                copies[original.id].combo_parent_id = copies[original.combo_parent_id.id]
            if original.tint_color_code and original.id in copies:
                copies[original.id].tint_color_code = original.tint_color_code
        return refund

    def _complete_reinvoice_cash_refund(self, source, session, refunds, invoices, confirmed):
        if confirmed is not True:
            raise UserError(_('Confirma que entregaste el efectivo del reembolso original.'))
        methods = source.payment_ids.filtered(lambda payment: payment.amount and not payment.is_change).payment_method_id
        if len(methods) != 1 or methods.type != 'cash' or methods not in session.config_id.payment_method_ids:
            raise UserError(_('La devolución automática solo está disponible para ventas cobradas íntegramente en efectivo.'))
        refund = self._prepare_guided_reinvoice_refund(source, session, refunds, invoices)
        self.env['res.company']._with_locked_records(refund)
        refund.invalidate_recordset()
        originals = refund.lines.refunded_orderline_id
        if (refund.state != 'draft' or refund.payment_ids or refund.account_move or refund.picking_ids
                or source.currency_id.compare_amounts(refund.amount_total, -source.amount_total)
                or originals != source.lines or len(refund.lines) != len(originals)
                or any(line.product_id != line.refunded_orderline_id.product_id
                       or line.product_uom_id.compare(line.qty, -line.refunded_orderline_id.qty)
                       or line.price_unit != line.refunded_orderline_id.price_unit
                       or line.discount != line.refunded_orderline_id.discount
                       or line.tax_ids != line.refunded_orderline_id.tax_ids
                       for line in refund.lines)):
            raise UserError(_('El borrador de devolución cambió. Revisa sus productos y pagos antes de refacturar.'))
        if source.picking_ids.filtered(lambda picking: picking.state == 'done') and not refund._should_create_picking_real_time():
            raise UserError(_('La caja está configurada para actualizar el almacén al cierre. Esta devolución necesita la entrada inmediata.'))
        refund.add_payment({'pos_order_id': refund.id, 'payment_method_id': methods.id, 'amount': refund.amount_total})
        refund.with_context(generate_pdf=False, sale_auto_invoice_checkout=True)._process_saved_order(draft=False)
        credit = refund.account_move
        if (refund.state != 'done' or credit.state != 'posted' or credit.move_type != 'out_refund'
                or credit.reversed_entry_id != invoices
                or source.currency_id.compare_amounts(credit.amount_total, invoices.amount_total)):
            raise UserError(_('Odoo no pudo contabilizar la devolución completa. No se generó el ticket de reemplazo.'))
        self._check_reinvoice_pos_refunds(source)
        return refund

    def _check_reinvoice_pos_refunds(self, source):
        if any(line.product_uom_id.compare(line.refunded_qty, line.qty) < 0 for line in source.lines if line.qty > 0):
            raise UserError(_('Registra primero la devolución completa del ticket original, incluido el reembolso.'))
        collected = source.payment_ids.filtered(lambda payment: payment.payment_method_id.type != 'pay_later')
        refunds = source.lines.refund_orderline_ids.order_id.filtered(lambda order: order.state in ('paid', 'done'))
        if refunds.lines.filtered(lambda line: line.refunded_orderline_id.order_id != source):
            raise UserError(_('Revisa el reembolso en Contabilidad: hay devoluciones que mezclan tickets.'))
        refunded = refunds.payment_ids.filtered(lambda payment: payment.payment_method_id.type != 'pay_later')
        if source.currency_id.compare_amounts(sum(collected.mapped('amount')) + sum(refunded.mapped('amount')), 0) > 0:
            raise UserError(_('El reembolso registrado es menor que el cobro de la venta original.'))

    def _return_reinvoice_stock(self, source, automatic):
        for picking in source.picking_ids.filtered(lambda picking: picking.state == 'done'):
            pending = {}
            for move in picking.move_ids.filtered(
                    lambda move: move.state == 'done' and move.location_dest_id.usage == 'customer'):
                returns = move.returned_move_ids.filtered(lambda item: item.state == 'done')
                if returns.returned_move_ids.filtered(lambda item: item.state != 'cancel'):
                    raise UserError(_('Revisa los movimientos de almacén de %s: hay devoluciones revertidas.', move.product_id.display_name))
                returned = sum(return_move.product_uom._compute_quantity(return_move.quantity, move.product_uom)
                               for return_move in returns)
                if move.product_uom.compare(returned, move.quantity) >= 0:
                    continue
                if (not automatic or move.product_id.tracking != 'none'
                        or move.location_id.usage != 'internal'
                        or move.returned_move_ids.filtered(lambda item: item.state not in ('cancel', 'done'))):
                    raise UserError(_('Valida primero la devolución de almacén de %s.', move.product_id.display_name))
                pending[move.id] = move.product_uom._compute_quantity(move.quantity - returned, move.product_id.uom_id)
            if not pending:
                continue
            wizard = self.env['stock.return.picking'].with_context(
                active_model='stock.picking', active_id=picking.id, active_ids=picking.ids,
            ).create({'picking_id': picking.id})
            for row in wizard.product_return_moves:
                row.quantity = pending.get(row.move_id.id, 0)
            receipt = wizard._create_return()
            for move in receipt.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            receipt.button_validate()
            if receipt.state != 'done':
                raise UserError(_('Completa la devolución de almacén antes de refacturar.'))

    def _reinvoice_pos_lines(self, source):
        rows = []
        for line in source.lines.sorted(lambda line: (bool(line.combo_parent_id), line.id)):
            if line.qty <= 0 or line.is_reward_line or line.advance_source_ref:
                raise UserError(_('El ticket tiene devoluciones, recompensas o anticipos que requieren revisión.'))
            rows.append((line.id, line.combo_parent_id.id, {
                'product_id': line.product_id.id, 'qty': line.qty, 'price_unit': line.price_unit,
                'discount': line.discount, 'tax_ids': [Command.set(line.tax_ids.ids)],
                'customer_note': line.customer_note, 'full_product_name': line.full_product_name,
                'tint_color_code': line.tint_color_code, 'price_type': 'manual',
                'combo_item_id': line.combo_item_id.id,
                'attribute_value_ids': [Command.set(line.attribute_value_ids.ids)],
                'custom_attribute_value_ids': [Command.create(values) for values in line.custom_attribute_value_ids.copy_data()],
            }))
        return rows

    def _reinvoice_sale_lines(self, source):
        rows = []
        for line in source.order_line.filtered(lambda line: not line.display_type and line.product_id).sorted(
                lambda line: (bool(line.combo_item_id), line.sequence, line.id)):
            if line.is_downpayment or line.product_uom_qty <= 0 or line.product_uom_id != line.product_id.uom_id:
                raise UserError(_('Revisa los anticipos, descuentos globales y unidades especiales antes de refacturar.'))
            recipe = line._tint_recipe()
            rows.append((line.id, line.linked_line_id.id if line.combo_item_id else False, {
                'product_id': line.product_id.id, 'qty': line.product_uom_qty, 'price_unit': line.price_unit,
                'discount': line.discount, 'tax_ids': [Command.set(line.tax_ids.ids)],
                'customer_note': line.name, 'tint_color_code': recipe.get('color_code') if recipe.get('base_only') else False,
                'price_type': 'manual', 'combo_item_id': line.combo_item_id.id,
                'attribute_value_ids': [Command.set(line.product_no_variant_attribute_value_ids.ids)],
                'custom_attribute_value_ids': [Command.create(values) for values in line.product_custom_attribute_value_ids.copy_data()],
            }))
            if recipe.get('base_only'):
                for index, dose in enumerate(recipe['doses']):
                    rows.append(((line.id, index), line.id, {
                        'product_id': dose['product_id'], 'qty': dose['points'] * line.product_uom_qty,
                        'price_unit': 0, 'tax_ids': [Command.clear()], 'price_type': 'manual',
                    }))
        return rows
