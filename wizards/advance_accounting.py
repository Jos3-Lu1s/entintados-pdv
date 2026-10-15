from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError


class PurchaseAdvanceInvoice(models.TransientModel):
    _name = 'purchase.advance.invoice'
    _description = 'Factura de anticipo de proveedor'

    purchase_id = fields.Many2one('purchase.order', required=True, readonly=True)
    currency_id = fields.Many2one(related='purchase_id.currency_id')
    amount_type = fields.Selection([('fixed', 'Importe fijo'), ('percentage', 'Porcentaje')], default='fixed', required=True)
    amount = fields.Monetary(string='Importe antes de impuestos')
    percentage = fields.Float(string='Porcentaje del subtotal')
    tax_ids = fields.Many2many('account.tax', string='Impuestos del comprobante', domain="[('type_tax_use', '=', 'purchase')]" )
    reference = fields.Char(string='Referencia del comprobante del proveedor', required=True)
    fiscal_kind = fields.Selection([
        ('on_account', 'Pago a cuenta de una operación definida'),
        ('fiscal', 'Anticipo fiscal (operación aún no definida)'),
    ], default='on_account', required=True)

    def action_create(self):
        self.ensure_one()
        order = self.purchase_id
        if order.state != 'purchase':
            raise UserError(_('Confirma la orden de compra antes de registrar su factura de anticipo.'))
        company = order.company_id
        if not company.supplier_advance_account_id or not company.advance_product_id:
            raise UserError(_('Configura primero la cuenta y el producto de anticipos en Ajustes de Contabilidad.'))
        company._check_advance_configuration()
        amount = self.amount
        if self.amount_type == 'percentage':
            if not 0 < self.percentage <= 100:
                raise UserError(_('El porcentaje debe ser mayor que cero y no superar 100.'))
            amount = order.currency_id.round(order.amount_untaxed * self.percentage / 100)
        if order.currency_id.compare_amounts(amount, 0) <= 0:
            raise UserError(_('El importe del anticipo debe ser positivo.'))
        if any(tax.company_id != company for tax in self.tax_ids):
            raise UserError(_('Los impuestos deben pertenecer a la compañía de la compra.'))
        previous = sum(order.advance_invoice_ids.filtered(lambda move: move.state != 'cancel').mapped('amount_untaxed'))
        if order.currency_id.compare_amounts(previous + amount, order.amount_untaxed) > 0:
            raise UserError(_('Los anticipos no pueden superar el subtotal de la orden de compra.'))
        invoice = self.env['account.move'].with_company(company).create({
            'move_type': 'in_invoice', 'partner_id': order.partner_id.id,
            'company_id': company.id, 'currency_id': order.currency_id.id,
            'invoice_date': fields.Date.context_today(self), 'ref': self.reference,
            'invoice_origin': order.name, 'advance_document': True,
            'advance_purchase_id': order.id, 'advance_fiscal_kind': self.fiscal_kind,
            'invoice_line_ids': [Command.create({
                'product_id': company.advance_product_id.id,
                'name': _('Anticipo de %s') % order.name,
                'account_id': company.supplier_advance_account_id.id,
                'quantity': 1, 'price_unit': amount, 'tax_ids': [Command.set(self.tax_ids.ids)],
            })],
        })
        return self.env['account.move']._action_open_moves(invoice, _('Factura de anticipo del proveedor'))


class AccountAdvanceApply(models.TransientModel):
    _name = 'account.advance.apply'
    _description = 'Aplicación de anticipo a factura'

    invoice_id = fields.Many2one('account.move', required=True, readonly=True)
    company_id = fields.Many2one(related='invoice_id.company_id')
    partner_id = fields.Many2one(related='invoice_id.commercial_partner_id')
    currency_id = fields.Many2one(related='invoice_id.currency_id')
    source_move_type = fields.Char(compute='_compute_source_move_type')
    source_id = fields.Many2one('account.move', string='Factura de anticipo', required=True)
    available = fields.Monetary(related='source_id.advance_available', string='Disponible')
    amount = fields.Monetary(string='Importe a aplicar con impuestos', required=True)
    mode = fields.Selection([
        ('deduction', 'Deducir en factura de remanente (borrador)'),
        ('credit_note', 'Preparar nota de aplicación (factura completa)'),
    ], default='deduction', required=True)

    @api.depends('invoice_id.move_type')
    def _compute_source_move_type(self):
        for wizard in self:
            wizard.source_move_type = 'in_invoice' if wizard.invoice_id.move_type == 'in_invoice' else 'out_invoice'

    @api.onchange('source_id')
    def _onchange_source(self):
        self.amount = min(self.available, self.invoice_id.amount_residual or self.invoice_id.amount_total)

    def action_apply(self):
        self.ensure_one()
        target, source = self.invoice_id, self.source_id
        source.check_access('read')
        if (target.move_type not in ('in_invoice', 'out_invoice') or target.is_advance_invoice
                or source.move_type != target.move_type or not source.is_advance_invoice
                or source.state != 'posted' or source.company_id != target.company_id
                or source.currency_id != target.currency_id or source.commercial_partner_id != target.commercial_partner_id):
            raise UserError(_('Selecciona una factura de anticipo contabilizada del mismo contacto, compañía y moneda.'))
        if (source.currency_id.compare_amounts(self.amount, 0) <= 0
                or source.currency_id.compare_amounts(self.amount, source.advance_available) > 0):
            raise UserError(_('El importe debe ser positivo y no superar el anticipo disponible.'))
        limit = target.amount_total if self.mode == 'deduction' else target.amount_residual
        if target.currency_id.compare_amounts(self.amount, limit) > 0:
            raise UserError(_('El anticipo aplicado no puede superar el importe pendiente de la factura.'))
        if self.mode == 'deduction' and target.state != 'draft':
            raise UserError(_('Solo puedes deducir anticipos en una factura en borrador.'))
        if self.mode == 'credit_note' and target.state != 'posted':
            raise UserError(_('Contabiliza la factura completa antes de preparar su nota de aplicación.'))
        account = source._advance_account()
        originals = source.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
        if not account or not originals or any(line.account_id != account for line in originals):
            raise UserError(_('La factura original debe utilizar la cuenta de anticipos configurada.'))
        if source.amount_total <= 0:
            raise UserError(_('La factura de anticipo debe tener un total positivo.'))
        ratio = self.amount / source.amount_total
        commands = []
        for original in originals:
            values = {
                'name': _('Aplicación de anticipo %s') % source.name,
                'product_id': original.product_id.id,
                'product_uom_id': original.product_uom_id.id,
                'quantity': original.quantity, 'discount': original.discount,
                'price_unit': original.price_unit * ratio * (-1 if self.mode == 'deduction' else 1),
                'account_id': account.id, 'advance_source_id': source.id,
                'tax_ids': [Command.set(original.tax_ids.ids)],
                'analytic_distribution': original.analytic_distribution,
            }
            commands.append(Command.create(values))
        original_total = target.amount_total
        if self.mode == 'deduction':
            document = target
            document.write({'invoice_line_ids': commands})
            applied = original_total - document.amount_total
        else:
            document = self.env['account.move'].with_company(target.company_id).create({
                'move_type': 'in_refund' if target.move_type == 'in_invoice' else 'out_refund',
                'partner_id': target.partner_id.id, 'company_id': target.company_id.id,
                'currency_id': target.currency_id.id, 'journal_id': target.journal_id.id,
                'invoice_date': fields.Date.context_today(self),
                'ref': _('Aplicación de anticipo %s a %s') % (source.name, target.name),
                'advance_target_id': target.id, 'invoice_line_ids': commands,
            })
            applied = document.amount_total
        if target.currency_id.compare_amounts(applied, self.amount):
            raise UserError(_('Los impuestos o redondeos dan una aplicación de %(actual)s en lugar de %(expected)s. Ajusta el importe.', actual=applied, expected=self.amount))
        if source.advance_fiscal_kind == 'fiscal':
            # Remnant relates to the advance; the application credit relates to the full invoice.
            origin = source if self.mode == 'deduction' else target
            uuid = getattr(origin, 'l10n_mx_edi_cfdi_uuid', False)
            if not uuid or 'l10n_mx_edi_cfdi_origin' not in document._fields:
                raise UserError(_('El flujo de anticipo fiscal requiere la localización mexicana y el UUID del CFDI de origen.'))
            relation = '07|%s' % uuid
            existing = document.l10n_mx_edi_cfdi_origin
            if existing and not existing.startswith('07|'):
                raise UserError(_('La factura ya tiene una relación fiscal distinta. Revisa los CFDI relacionados antes de aplicar otro anticipo.'))
            if existing:
                relation = '07|' + ','.join(sorted(set([uuid] + [value.strip() for value in existing[3:].split(',')])))
            document.l10n_mx_edi_cfdi_origin = relation
        document._check_advance_applications()
        return document._action_open_moves(document, _('Documento de aplicación de anticipo'))
