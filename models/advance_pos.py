from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PosConfig(models.Model):
    _inherit = 'pos.config'

    advance_product_id = fields.Many2one(related='company_id.advance_product_id')
    advance_risk_enabled = fields.Boolean(related='company_id.advance_risk_enabled')

    def _get_special_products(self):
        return super()._get_special_products() | self.env.companies.advance_product_id

    @api.model
    def _load_pos_data_fields(self, config):
        field_names = super()._load_pos_data_fields(config)
        # An empty list means "read every field" in Odoo's POS loader.
        # Replacing it with only our additions drops company_id/use_pricelist
        # and prevents both the server loader and the JS tax engine starting.
        if not field_names:
            return field_names
        return list(dict.fromkeys(field_names + ['advance_product_id', 'advance_risk_enabled']))


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    advance_source_ref = fields.Integer(string='Identificador de factura de anticipo', copy=False)

    def _is_accounting_advance_line(self):
        self.ensure_one()
        return bool(self.product_id.is_accounting_advance or self.advance_source_ref or (
            self.company_id.advance_risk_enabled and self.sale_order_origin_id
            and self.product_id == self.order_id.config_id.down_payment_product_id
        ))

    def _accounting_advance_source(self):
        self.ensure_one()
        if self.advance_source_ref:
            return self.env['account.move'].sudo().browse(self.advance_source_ref).exists()
        if self.price_unit * self.qty < 0 and self.sale_order_line_id.is_downpayment:
            sources = self.sale_order_line_id._accounting_advance_sources()
            if len(sources) == 1:
                return sources
        return self.env['account.move']

    @api.model
    def _load_pos_data_fields(self, config):
        return list(dict.fromkeys(super()._load_pos_data_fields(config) + ['advance_source_ref']))


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _check_pos_advances(self):
        for order in self:
            lines = order.lines.filtered(lambda line: line._is_accounting_advance_line())
            if not lines:
                continue
            if not order.partner_id or not order.company_id.advance_risk_enabled:
                raise UserError(_('Los anticipos requieren un cliente identificado y la integración contable activada.'))
            if order.invoice_split_count > 1 or order.config_id.cash_rounding:
                raise UserError(_('Los anticipos no admiten facturas divididas ni redondeo de efectivo.'))
            if not order.to_invoice:
                raise UserError(_('Los pedidos con anticipos deben generar factura.'))
            positive = lines.filtered(lambda line: line.price_unit * line.qty > 0)
            if positive and (len(lines) != len(order.lines) or lines.filtered('advance_source_ref')):
                raise UserError(_('Cobra el anticipo en un pedido separado de la venta o de su aplicación.'))
            if positive and order.payment_ids.filtered(lambda payment: payment.payment_method_id.type == 'pay_later'):
                raise UserError(_('Un anticipo debe cobrarse con efectivo o un método real de pago; no puede cobrarse a crédito.'))
            native_pending = positive.filtered(lambda line: line.sale_order_origin_id and not line.sale_order_line_id)
            for sale in native_pending.sale_order_origin_id:
                self.env.cr.execute('SELECT id FROM sale_order WHERE id = %s FOR UPDATE', [sale.id])
                sale.invalidate_recordset(['amount_unpaid'])
                amount = sum(native_pending.filtered(lambda line: line.sale_order_origin_id == sale).mapped('price_subtotal_incl'))
                if (sale.company_id != order.company_id or sale.currency_id != order.currency_id
                        or sale.partner_id.commercial_partner_id != order.partner_id.commercial_partner_id
                        or sale.state == 'cancel'
                        or sale.currency_id.compare_amounts(amount, sale.amount_unpaid) > 0):
                    raise UserError(_('El anticipo no corresponde al pedido seleccionado o supera su saldo pendiente.'))
            for line in lines:
                if not line.price_unit * line.qty:
                    raise UserError(_('El importe del anticipo debe ser distinto de cero.'))
                native = bool(line.sale_order_origin_id and line.product_id == order.config_id.down_payment_product_id)
                if line.discount or not (line.product_id.is_accounting_advance or native):
                    raise UserError(_('Las líneas de anticipo no admiten descuentos y deben utilizar el producto de anticipos.'))
                source = line._accounting_advance_source()
                if line.price_unit * line.qty < 0 and not source:
                    raise UserError(_('Selecciona el anticipo original antes de aplicar o devolver un saldo.'))
                if source:
                    if (not source or not source.is_advance_invoice or source.state != 'posted'
                            or source.move_type != 'out_invoice' or source.company_id != order.company_id
                            or source.currency_id != order.currency_id
                            or source.commercial_partner_id != order.partner_id.commercial_partner_id):
                        raise UserError(_('El anticipo seleccionado no corresponde al cliente, compañía o moneda del pedido.'))
                    if source.advance_fiscal_kind == 'fiscal':
                        raise UserError(_('Aplica los anticipos fiscales desde Contabilidad para preparar y revisar sus CFDI relacionados.'))
            if order.currency_id.compare_amounts(order.amount_total, 0) < 0:
                raise UserError(_('Las devoluciones de anticipos se realizan desde Contabilidad con su documento de devolución.'))

    @api.model
    def _process_order(self, order, existing_order):
        order_id = super()._process_order(order, existing_order)
        record = self.browse(order_id)
        record._check_pos_advances()
        if (record.state in ('paid', 'done') and record.account_move.state != 'posted'
                and (record.pos_payment_flow in ('ppd_partial', 'credit')
                     or record.lines.filtered(lambda line: line._is_accounting_advance_line())
                     or any(payment.amount > 0 and payment.payment_method_id.type == 'pay_later' for payment in record.payment_ids))):
            raise UserError(_('No se pudo generar la factura de la operación. Revisa el saldo disponible, los datos del cliente y vuelve a sincronizar.'))
        return order_id

    def _process_saved_order(self, draft):
        # POS synchronizes other draft tickets together with the paid ticket.
        # Enforce the invoice on the persisted lines before native payment and
        # invoice processing, including drafts from older browser sessions.
        self.ensure_one()
        if (self.state != 'cancel' and not self.to_invoice
                and any(line._is_accounting_advance_line() for line in self.lines)):
            self.to_invoice = True
        return super()._process_saved_order(draft)

    def action_pos_order_paid(self):
        self._check_pos_advances()
        # New UI orders may already arrive with state='paid'. The native
        # pipeline calls this before generating their invoice in either case.
        self._check_pos_deferred_payments(check_risk=not bool(self.account_move))
        return super().action_pos_order_paid()

    @api.model
    def _get_invoice_lines_values(self, line_values, pos_line, move_type):
        values = super()._get_invoice_lines_values(line_values, pos_line, move_type)
        if pos_line._is_accounting_advance_line():
            account = pos_line.company_id.downpayment_account_id
            if account and pos_line.order_id.fiscal_position_id:
                account = pos_line.order_id.fiscal_position_id.map_account(account)
            if not account:
                raise UserError(_('Configura la cuenta de anticipos de clientes antes de facturar en caja.'))
            values['account_id'] = account.id
        source = pos_line._accounting_advance_source()
        if source:
            values['advance_source_id'] = source.id
            values['account_id'] = source._advance_account().id
        return values

    def _prepare_invoice_vals(self):
        self._check_pos_advances()
        values = super()._prepare_invoice_vals()
        if self.lines and all(line._is_accounting_advance_line() and line.price_unit * line.qty > 0 for line in self.lines):
            values['advance_document'] = True
        return values
