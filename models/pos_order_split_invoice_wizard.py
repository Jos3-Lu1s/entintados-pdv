# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosOrderSplitInvoiceWizard(models.TransientModel):
    _name = "pos.order.split.invoice.wizard"
    _description = "Facturar una orden del POS en varias facturas"

    order_id = fields.Many2one("pos.order", string="Orden", required=True, readonly=True)
    currency_id = fields.Many2one(related="order_id.currency_id")
    amount_total = fields.Monetary(related="order_id.amount_total", currency_field="currency_id")
    invoice_count = fields.Integer(string="Número de facturas", default=2, required=True)
    amount_per_invoice = fields.Monetary(
        string="Importe aproximado por factura",
        compute="_compute_amount_per_invoice", currency_field="currency_id")

    @api.depends("invoice_count", "amount_total")
    def _compute_amount_per_invoice(self):
        for wiz in self:
            wiz.amount_per_invoice = (
                wiz.amount_total / wiz.invoice_count if wiz.invoice_count > 0 else 0.0)

    def action_confirm(self):
        self.ensure_one()
        order = self.order_id
        if self.invoice_count < 1:
            raise UserError(_("El número de facturas debe ser al menos 1."))
        if order.account_move:
            raise UserError(_("Esta orden ya tiene factura."))
        if order.is_refund and self.invoice_count > 1:
            raise UserError(_("Los reembolsos no se pueden dividir en varias facturas."))
        order.invoice_split_count = self.invoice_count
        order.action_pos_order_invoice()
        return order.action_view_invoice()