# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    invoice_split_count = fields.Integer(string="Facturas a generar", default=1, copy=False)
    split_invoice_ids = fields.Many2many(
        "account.move", "pos_order_split_invoice_rel", "order_id", "move_id",
        string="Facturas divididas", copy=False,
    )
    entintados_invoice_count = fields.Integer(
        string="Facturas", compute="_compute_entintados_invoice_count")

    def _entintados_split_move_vals(self, move_vals, n):
        """n copias de move_vals con cantidades repartidas; la última absorbe el residuo."""
        prec = self.env["decimal.precision"].precision_get("Product Unit of Measure")
        vals_list = []
        for i in range(n):
            vals = dict(move_vals)
            lines = []
            for cmd in move_vals.get("invoice_line_ids", []):
                if cmd[0] == 0 and isinstance(cmd[2], dict) and not cmd[2].get("display_type"):
                    line = dict(cmd[2])
                    total_qty = line.get("quantity", 0.0)
                    part = float_round(total_qty / n, precision_digits=prec)
                    line["quantity"] = part if i < n - 1 else total_qty - part * (n - 1)
                    lines.append((0, cmd[1], line))
                else:
                    lines.append(cmd)
            vals["invoice_line_ids"] = lines
            if vals.get("ref"):
                vals["ref"] = "%s (%d/%d)" % (vals["ref"], i + 1, n)
            if i > 0:
                # Solo la primera factura queda ligada a la orden (account_move)
                vals.pop("pos_order_ids", None)
                vals.pop("pos_refunded_invoice_ids", None)
            vals_list.append(vals)
        return vals_list

    def _create_invoice(self, move_vals):
        n = self.invoice_split_count if len(self) == 1 else 1
        _logger.info("[ENTINTADOS] _create_invoice orden=%s n=%s", self.mapped("name"), n)
        if n <= 1 or self.is_refund:
            return super()._create_invoice(move_vals)
        if self.config_id.cash_rounding:
            raise UserError(_("No se puede dividir la factura cuando el POS usa redondeo de efectivo."))

        AccountMove = self.env["account.move"].sudo().with_company(self.company_id)
        invoices = self.env["account.move"].browse()
        for i, vals in enumerate(self._entintados_split_move_vals(move_vals, n), start=1):
            invoice = AccountMove.with_context(
                default_move_type=vals["move_type"], linked_to_pos=True
            ).create(vals)
            invoice.message_post(body=_(
                "Factura %(i)s de %(n)s creada desde el punto de venta (orden %(order)s).",
                i=i, n=n, order=self.name,
            ))
            invoices |= invoice
        self._entintados_adjust_rounding(invoices)
        self.split_invoice_ids = invoices
        return invoices[0]

    def _reconcile_invoice_payments(self, invoice, payment_moves):
        res = super()._reconcile_invoice_payments(invoice, payment_moves)
        if len(self) != 1:
            return res
        extras = self.split_invoice_ids - invoice
        if not extras:
            return res

        company = self.company_id
        extras.filtered(lambda m: m.state == "draft").sudo().with_company(company) \
            .with_context(**self._get_invoice_post_context())._post()

        receivable_account = self.env["res.partner"]._find_accounting_partner(
            invoice.partner_id).with_company(company).property_account_receivable_id
        if receivable_account.reconcile:
            payment_lines = payment_moves.pos_payment_ids \
                ._get_receivable_lines_for_invoice_reconciliation(receivable_account) \
                .filtered(lambda l: not l.reconciled)
            extra_lines = extras.line_ids.filtered(
                lambda l: l.account_id == receivable_account and not l.reconciled)
            (payment_lines | extra_lines).sudo().with_company(company).reconcile()

        if self.env.context.get("generate_pdf", True):
            for extra in extras:
                extra.with_context(skip_invoice_sync=True)._generate_and_send()
        return res

    def _entintados_adjust_rounding(self, invoices):
        """La última factura absorbe el desfase de redondeo para que la suma
        de las facturas sea igual al total de la orden."""
        currency = self.currency_id
        last = invoices[-1]
        target = currency.round(self.amount_total - sum(invoices[:-1].mapped("amount_total")))
        diff = currency.round(last.amount_total - target)
        if currency.is_zero(diff) or abs(diff) > 0.05:
            return

        lines = last.invoice_line_ids.filtered(lambda l: not l.display_type and l.quantity)
        if not lines:
            return
        line = max(lines, key=lambda l: l.price_subtotal)
        original = line.price_unit
        sign = 1 if diff > 0 else -1

        for k in range(1, 8):
            line.price_unit = original - sign * 0.01 * k
            if currency.is_zero(last.amount_total - target):
                _logger.info("[ENTINTADOS] redondeo ajustado: precio %s -> %s",
                             original, line.price_unit)
                return
        line.price_unit = original
        _logger.warning("[ENTINTADOS] no se pudo ajustar el redondeo (diff=%s)", diff)
    
    @api.depends("account_move", "split_invoice_ids")
    def _compute_entintados_invoice_count(self):
        for order in self:
            order.entintados_invoice_count = len(order.account_move | order.split_invoice_ids)

    def action_view_invoice_custom(self):
        self.ensure_one()
        invoices = self.account_move | self.split_invoice_ids
        if len(invoices) <= 1:
            return super().action_view_invoice()
        return {
            "name": _("Facturas de cliente"),
            "view_mode": "list,form",
            "res_model": "account.move",
            "type": "ir.actions.act_window",
            "domain": [("id", "in", invoices.ids)],
            "context": {"default_move_type": "out_invoice"},
        }