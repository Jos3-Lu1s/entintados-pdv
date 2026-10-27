from odoo import api, models


def allocate_amount(amount, weights, rounding):
    """Distribute a ticket payment without losing cents or duplicating totals."""
    total = sum(weights)
    if not total:
        return [0.0] * len(weights)
    parts = [rounding(amount * weight / total) for weight in weights[:-1]]
    return parts + [amount - sum(parts)]


class PosSaleDetails(models.AbstractModel):
    _inherit = 'report.point_of_sale.report_saledetails'

    @api.model
    def get_sale_details(self, date_start=False, date_stop=False, config_ids=False,
                         session_ids=False, **kwargs):
        result = super().get_sale_details(
            date_start, date_stop, config_ids, session_ids, **kwargs)
        orders = self.env['pos.order'].search(self._get_domain(
            date_start, date_stop, config_ids, session_ids, **kwargs))
        related_invoices = (orders.account_move | orders.split_invoice_ids
                            | orders.lines.settled_invoice_id
                            | orders.lines.settled_order_id.account_move
                            | orders.lines.settled_order_id.split_invoice_ids)
        invoice_orders = {}
        if related_invoices:
            origin_orders = self.env['pos.order'].search([
                '|', ('account_move', 'in', related_invoices.ids),
                ('split_invoice_ids', 'in', related_invoices.ids),
            ], order='id')
            for origin in origin_orders:
                for invoice in origin.account_move | origin.split_invoice_ids:
                    invoice_orders.setdefault(invoice.id, []).append(origin.name)
        groups = {}
        for order in orders:
            targets = {}
            for line in order.lines:
                settled = line.settled_invoice_id
                if not settled and line.settled_order_id:
                    settled = (line.settled_order_id.account_move
                               | line.settled_order_id.split_invoice_ids)
                if settled:
                    amounts = allocate_amount(
                        line.price_subtotal_incl, settled.mapped('amount_total'),
                        order.currency_id.round)
                    for invoice, amount in zip(settled, amounts):
                        targets[invoice] = targets.get(invoice, 0.0) + amount
            invoices = (order.account_move | order.split_invoice_ids).filtered(
                lambda move: move.state == 'posted')
            if not targets:
                targets = {invoice: invoice.amount_total for invoice in invoices}
            else:
                remainder = order.currency_id.round(order.amount_total - sum(targets.values()))
                if not order.currency_id.is_zero(remainder):
                    targets[False] = remainder
            # Keep tickets without invoices visible so the method subtotal reconciles.
            if not targets or order.currency_id.is_zero(sum(targets.values())):
                targets = {False: order.amount_total or 1.0}
            weights = list(targets.values())
            numbered_invoices = sorted((invoice for invoice in targets if invoice), key=lambda invoice: invoice.id)
            invoice_labels = {
                invoice.id: f'Factura {index} de {len(numbered_invoices)}'
                for index, invoice in enumerate(numbered_invoices, start=1)
            } if len(numbered_invoices) > 1 else {}
            for payment in order.payment_ids:
                method = payment.payment_method_id
                key = (method.id, order.currency_id.id)
                group = groups.setdefault(key, {
                    'name': method.name, 'credit': method.type == 'pay_later',
                    'currency': order.currency_id, 'rows': {}, 'total': 0.0,
                })
                group['total'] += payment.amount
                amounts = allocate_amount(payment.amount, weights, order.currency_id.round)
                for invoice, amount in zip(targets, amounts):
                    if not amount:
                        continue
                    row_key = invoice.id if invoice else ('ticket', order.id)
                    row = group['rows'].setdefault(row_key, {
                        'invoice': invoice.name if invoice else 'Sin factura · ' + order.name,
                        'order_reference': (', '.join(invoice_orders.get(invoice.id, []))
                                            or invoice.invoice_origin or '—') if invoice else order.name,
                        'customer': (invoice.partner_id if invoice else order.partner_id).display_name or 'Público en general',
                        'amount': 0.0,
                        'allocated': len(targets) > 1,
                        'invoice_part': invoice_labels.get(invoice.id, '') if invoice else '',
                    })
                    row['amount'] += amount
                    row['allocated'] = row['allocated'] or len(targets) > 1
                    if invoice and invoice_labels.get(invoice.id):
                        row['invoice_part'] = invoice_labels[invoice.id]
        result['invoice_payment_groups'] = [
            dict(group, rows=sorted(group['rows'].values(), key=lambda row: row['invoice']))
            for group in groups.values()
        ]
        return result
