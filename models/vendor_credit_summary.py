import math
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class VendorCreditSummary(models.Model):
    _inherit = 'vendor.credit.group'

    # Historical groups may have no supplier reference. New groups must supply it;
    # retaining an empty historical value avoids inventing a supplier document number.
    supplier_reference = fields.Char(string='Folio de referencia', copy=False, tracking=True)
    amount_untaxed = fields.Monetary(string='Subtotal', compute='_compute_credit_breakdown')
    amount_iva = fields.Monetary(string='IVA', compute='_compute_credit_breakdown')
    amount_other_taxes = fields.Monetary(string='Otros impuestos', compute='_compute_credit_breakdown')
    amount_withholding = fields.Monetary(string='Retenciones', compute='_compute_credit_breakdown')
    amount_tax = fields.Monetary(string='Impuestos netos', compute='_compute_credit_breakdown')
    amount_rounding = fields.Monetary(string='Ajuste de redondeo', compute='_compute_credit_breakdown')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            reference = (vals.get('supplier_reference') or '').strip()
            if not reference:
                raise UserError(_('Captura el folio de referencia de la nota de crédito del proveedor.'))
            vals['supplier_reference'] = reference
        return super().create(vals_list)

    def write(self, vals):
        if 'supplier_reference' in vals:
            vals = dict(vals, supplier_reference=(vals['supplier_reference'] or '').strip())
            if not vals['supplier_reference']:
                raise UserError(_('El folio de referencia no puede quedar vacío.'))
        return super().write(vals)

    def _create_group_credits(self):
        if not (self.supplier_reference or '').strip():
            raise UserError(_('Captura el folio de referencia antes de generar las notas.'))
        return super()._create_group_credits()

    @api.depends('line_ids.selected', 'line_ids.amount', 'currency_id', 'reason_id.operation_type',
                 'line_ids.invoice_id.amount_total', 'line_ids.invoice_id.invoice_line_ids.price_unit',
                 'line_ids.invoice_id.invoice_line_ids.quantity', 'line_ids.invoice_id.invoice_line_ids.discount',
                 'line_ids.invoice_id.invoice_line_ids.tax_ids', 'line_ids.credit_id.amount_total',
                 'line_ids.invoice_id.invoice_line_ids.tax_ids.l10n_mx_tax_type',
                 'line_ids.credit_id.amount_untaxed', 'line_ids.credit_id.amount_tax', 'line_ids.credit_id.state',
                 'line_ids.credit_id.invoice_line_ids.tax_ids',
                 'line_ids.credit_id.invoice_line_ids.tax_ids.l10n_mx_tax_type',
                 'material_line_ids.quantity', 'material_line_ids.invoice_line_id', 'material_line_ids.allocation_id')
    def _compute_credit_breakdown(self):
        for group in self:
            base_total = iva = other = withholding = 0.0
            complete = True
            for allocation in group.line_ids.filtered('selected'):
                credit = allocation.credit_id.filtered(lambda move: move.state != 'cancel')
                if credit:
                    source = [(line, line.quantity, line.price_unit) for line in credit.invoice_line_ids
                              if line.display_type == 'product']
                    base_total += credit.amount_untaxed
                else:
                    base, lines = allocation._credit_source_lines(group.reason_id)
                    if base <= 0 or not math.isfinite(allocation.amount):
                        complete = False
                        continue
                    if group.reason_id.operation_type == 'return':
                        source = [(line, quantity, line.price_unit * allocation.amount / base)
                                  for line, quantity in lines]
                    else:
                        source = [(line, 1, line.price_unit * quantity * allocation.amount / base)
                                  for line, quantity in lines]
                for line, quantity, price in source:
                    # Use the same tax engine, discount and taxable quantities as the credit.
                    result = line.tax_ids.compute_all(price * (1 - line.discount / 100),
                        currency=group.currency_id, quantity=quantity,
                        product=line.product_id if credit or group.reason_id.operation_type == 'return' else False,
                        partner=group.partner_id, is_refund=True)
                    if not credit:
                        base_total += result['total_excluded']
                    for item in result['taxes']:
                        tax = self.env['account.tax'].browse(item['id'])
                        amount = item['amount']
                        if amount < 0:
                            withholding += amount
                        elif (tax.l10n_mx_tax_type == 'iva' or (not tax.l10n_mx_tax_type
                                and re.search(r'\b(IVA|VAT)\b', '%s %s' % (tax.tax_group_id.name, tax.name), re.I))):
                            iva += amount
                        else:
                            other += amount
            rounding = group.currency_id.round
            group.amount_untaxed = rounding(base_total)
            group.amount_iva = rounding(iva)
            group.amount_other_taxes = rounding(other)
            group.amount_withholding = rounding(withholding)
            group.amount_tax = group.amount_iva + group.amount_other_taxes + group.amount_withholding
            group.amount_rounding = rounding(group.amount_total - group.amount_untaxed - group.amount_tax) if complete else 0
