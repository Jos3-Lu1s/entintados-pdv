from collections import defaultdict

from odoo import fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain


ORIGINS = [('pos', 'Punto de Venta'), ('sale', 'Ventas')]


class BranchCashCut(models.TransientModel):
    _name = 'branch.cash.cut'
    _description = 'Corte de caja por sucursal'

    company_id = fields.Many2one('res.company', string='Sucursal', required=True, default=lambda self: self.env.company)
    date_from = fields.Date(string='Desde', required=True, default=lambda self: fields.Date.context_today(self))
    date_to = fields.Date(string='Hasta', required=True, default=lambda self: fields.Date.context_today(self))
    include_pos = fields.Boolean(string='Punto de Venta', default=True)
    include_sale = fields.Boolean(string='Ventas', default=True)

    def _payment_domain(self, invoice_side, payment_side):
        invoice = f'{invoice_side}.move_id'
        payment = f'{payment_side}.move_id'
        return Domain([
            (f'{invoice}.company_id', '=', self.company_id.id),
            (f'{invoice}.state', '=', 'posted'),
            (f'{invoice}.move_type', 'in', ['out_invoice', 'out_refund']),
            (f'{invoice_side}.account_type', '=', 'asset_receivable'),
            (f'{payment}.company_id', '=', self.company_id.id),
            (f'{payment}.state', '=', 'posted'),
            (f'{payment}.move_type', '=', 'entry'),
            (f'{payment}.date', '>=', self.date_from),
            (f'{payment}.date', '<=', self.date_to),
        ])

    def _payment_details(self, move, refund):
        payment = move.origin_payment_id
        # Point of Sale invoice payments may be journal entries without an account.payment.
        pos_lines = move.sudo().pos_payment_ids.filtered(
            lambda line: line.company_id == self.company_id and not line.is_change
            and line.payment_method_id.type != 'pay_later')
        pos_line = pos_lines[:1]
        method = payment.sudo().pos_payment_method_id or pos_line.payment_method_id
        if payment and (payment.state not in ('paid', 'in_process') or
                        payment.payment_type != ('outbound' if refund else 'inbound')):
            return False
        if not (payment or move.statement_line_id or pos_line):
            return False
        if pos_line and not payment and (pos_line.amount < 0) != refund:
            return False
        if move.statement_line_id and (move.statement_line_id.amount < 0) != refund:
            return False
        journal = method.journal_id or move.journal_id
        code = (method.l10n_mx_edi_payment_method_id or move.l10n_mx_edi_payment_method_id).code
        kind = {
            '01': 'Efectivo', '02': 'Cheque', '03': 'Transferencia', '04': 'Tarjeta',
            '28': 'Tarjeta', '29': 'Tarjeta',
        }.get(code) or {
            'transfer': 'Transferencia', 'check': 'Cheque', 'card': 'Tarjeta',
        }.get(method.collection_type) or ('Efectivo' if journal.type == 'cash' else 'Otro')
        order = payment.sudo().pos_invoice_collection_order_id or pos_line.pos_order_id
        return kind, method.terminal_label or (journal.name if journal.type != 'cash' else 'Efectivo'), order

    def _collect_rows(self):
        amounts = defaultdict(float)
        for invoice_side, payment_side in [('debit_move_id', 'credit_move_id'), ('credit_move_id', 'debit_move_id')]:
            for match in self.env['account.partial.reconcile'].search(self._payment_domain(invoice_side, payment_side)):
                invoice = match[invoice_side].move_id
                move = match[payment_side].move_id
                amounts[invoice.id, move.id] += match.amount
        moves = self.env['account.move']
        credit_invoices = moves.search([
            ('company_id', '=', self.company_id.id), ('state', '=', 'posted'),
            ('move_type', '=', 'out_invoice'), ('l10n_mx_edi_payment_policy', '=', 'PPD'),
            ('invoice_date', '>=', self.date_from), ('invoice_date', '<=', self.date_to),
        ])
        invoices = moves.browse(sorted({invoice_id for invoice_id, _ in amounts})) | credit_invoices
        invoice_by_id = {invoice.id: invoice for invoice in invoices}
        payment_by_id = {move.id: move for move in moves.browse(sorted({move_id for _, move_id in amounts}))}
        source_invoices = invoices | invoices.reversed_entry_id
        orders_by_invoice = {}
        if source_invoices:
            orders = self.env['pos.order'].sudo().search([
                ('company_id', '=', self.company_id.id), '|',
                ('account_move', 'in', source_invoices.ids), ('split_invoice_ids', 'in', source_invoices.ids),
            ], order='id')
            for order in orders:
                for invoice in order.account_move | order.split_invoice_ids:
                    orders_by_invoice.setdefault(invoice.id, order)

        def invoice_values(invoice):
            order = orders_by_invoice.get(invoice.id) or orders_by_invoice.get(invoice.reversed_entry_id.id)
            sales = (invoice | invoice.reversed_entry_id).sudo().invoice_line_ids.sale_line_ids.order_id
            origin = 'pos' if order else 'sale' if sales else False
            if not origin or (origin == 'pos' and not self.include_pos) or (origin == 'sale' and not self.include_sale):
                return False
            return {
                'cut_id': self.id, 'invoice_id': invoice.id, 'partner_id': invoice.partner_id.id,
                'currency_id': self.company_id.currency_id.id, 'origin': origin,
                'order_reference': order.name if order else ', '.join(sales.mapped('name')),
            }

        values_by_id = {invoice.id: invoice_values(invoice) for invoice in invoices}
        rows = []
        for (invoice_id, move_id), amount in amounts.items():
            invoice = invoice_by_id[invoice_id]
            move = payment_by_id[move_id]
            values = values_by_id[invoice_id]
            refund = invoice.move_type == 'out_refund'
            details = values and self._payment_details(move, refund)
            if not details or self.company_id.currency_id.is_zero(amount):
                continue
            method, subtype, order = details
            rows.append(dict(
                values, date=move.date, payment_move_id=move.id, method=method, subtype=subtype,
                cashier=order.user_id.name if order else False, register=order.config_id.name if order else False,
                refund=amount if refund else 0,
                advances=amount if not refund and invoice.is_advance_invoice else 0,
                credit_receipts=amount if not refund and not invoice.is_advance_invoice and invoice.l10n_mx_edi_payment_policy == 'PPD' else 0,
                cash_sales=amount if not refund and not invoice.is_advance_invoice and invoice.l10n_mx_edi_payment_policy != 'PPD' else 0,
                net=-amount if refund else amount,
            ))
        for invoice in credit_invoices.filtered(lambda move: not move.is_advance_invoice):
            values = values_by_id[invoice.id]
            if values:
                rows.append(dict(values, date=invoice.invoice_date, method='Facturado a crédito',
                                 credit_billed=abs(invoice.amount_total_signed)))
        return rows

    def _check_cut_filters(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_readonly') and not self.env.user.has_group('account.group_account_invoice'):
            raise AccessError(_('Se requiere acceso de Contabilidad para generar el corte.'))
        if self.company_id not in self.env.companies:
            raise AccessError(_('Selecciona una sucursal activa entre tus compañías permitidas.'))
        if self.date_from > self.date_to:
            raise UserError(_('La fecha inicial debe ser anterior o igual a la fecha final.'))
        if not (self.include_pos or self.include_sale):
            raise UserError(_('Selecciona al menos un origen: Punto de Venta o Ventas.'))

    def action_print_cut(self):
        self._check_cut_filters()
        return self.env.ref('entintados_pdv.action_branch_cash_cut_pdf').report_action(self, config=False)

    def action_open_cut(self):
        self._check_cut_filters()
        self.env['branch.cash.cut.line'].search([('cut_id', '=', self.id)]).unlink()
        self.env['branch.cash.cut.line'].create(self._collect_rows())
        return {
            'type': 'ir.actions.act_window', 'name': _('Corte: %s · %s a %s', self.company_id.name, self.date_from, self.date_to),
            'res_model': 'branch.cash.cut.line', 'view_mode': 'list',
            'views': [(self.env.ref('entintados_pdv.branch_cash_cut_line_list').id, 'list')],
            'domain': [('cut_id', '=', self.id)],
            'context': {'group_by': ['method', 'subtype'], 'create': False, 'edit': False,
                        'delete': False, 'branch_cash_cut_id': self.id},
        }


class BranchCashCutLine(models.TransientModel):
    _name = 'branch.cash.cut.line'
    _description = 'Detalle de corte de caja por sucursal'
    _order = 'date desc, id desc'

    cut_id = fields.Many2one('branch.cash.cut', required=True, ondelete='cascade', index=True)
    date = fields.Date(string='Fecha', required=True)
    invoice_id = fields.Many2one('account.move', string='Factura / nota de crédito', required=True, ondelete='cascade')
    payment_move_id = fields.Many2one('account.move', string='Asiento del cobro', ondelete='cascade')
    partner_id = fields.Many2one('res.partner', string='Cliente', ondelete='set null')
    origin = fields.Selection(ORIGINS, string='Origen', required=True)
    order_reference = fields.Char(string='Orden / ticket')
    method = fields.Char(string='Método', required=True)
    subtype = fields.Char(string='Subtipo')
    register = fields.Char(string='Caja')
    cashier = fields.Char(string='Usuario')
    currency_id = fields.Many2one('res.currency', required=True)
    cash_sales = fields.Monetary(string='Contado cobrado')
    credit_receipts = fields.Monetary(string='Abonos a crédito')
    advances = fields.Monetary(string='Anticipos recibidos')
    refund = fields.Monetary(string='Devoluciones')
    net = fields.Monetary(string='Total neto')
    credit_billed = fields.Monetary(string='Facturado a crédito')

    def _get_header_cut(self):
        cut = self.env['branch.cash.cut'].browse(self.env.context.get('branch_cash_cut_id')).exists()
        if not cut:
            raise UserError(_('Vuelve a generar el corte para usar sus filtros.'))
        cut.check_access('read')
        cut._check_cut_filters()
        return cut

    def action_print_general(self):
        return self._get_header_cut().action_print_cut()

    def action_print_detail(self):
        cut = self._get_header_cut()
        return self.env.ref('entintados_pdv.action_branch_cash_cut_detail_pdf').report_action(cut, config=False)

    def action_change_filters(self):
        cut = self._get_header_cut()
        action = self.env['ir.actions.actions']._for_xml_id('entintados_pdv.branch_cash_cut_action')
        action['context'] = {
            f'default_{field}': cut[field].id if field == 'company_id' else cut[field]
            for field in ('company_id', 'date_from', 'date_to', 'include_pos', 'include_sale')
        }
        return action


class BranchCashCutReport(models.AbstractModel):
    _name = 'report.entintados_pdv.branch_cash_cut_pdf'
    _description = 'Resumen imprimible de corte de caja'

    def _get_report_values(self, docids, data=None):
        cut = self.env['branch.cash.cut'].browse(docids)
        cut.check_access('read')
        cut._check_cut_filters()
        columns = ('cash_sales', 'credit_receipts', 'advances', 'refund', 'net')
        summary = defaultdict(lambda: dict.fromkeys(columns, 0.0))
        details = defaultdict(list)
        for row in cut._collect_rows():
            if not row.get('payment_move_id'):
                continue
            group = (row['method'], row.get('subtype') or '')
            bucket = summary[group]
            for key in columns:
                bucket[key] += row[key]
            details[group].append(row)
        invoice_names = dict(self.env['account.move'].browse(sorted({
            row['invoice_id'] for rows in details.values() for row in rows
        })).mapped(lambda invoice: (invoice.id, invoice.name)))
        partner_names = dict(self.env['res.partner'].browse(sorted({
            row['partner_id'] for rows in details.values() for row in rows if row['partner_id']
        })).mapped(lambda partner: (partner.id, partner.display_name)))
        return {
            'doc_ids': cut.ids, 'doc_model': cut._name, 'docs': cut,
            'columns': columns,
            'summary': sorted(summary.items()),
            'details': {group: sorted(rows, key=lambda row: (row['date'], row['invoice_id']))
                        for group, rows in details.items()},
            'invoice_names': invoice_names, 'partner_names': partner_names, 'origins': dict(ORIGINS),
            'totals': {key: sum(row[key] for row in summary.values()) for key in columns},
        }


class BranchCashCutDetailReport(models.AbstractModel):
    _name = 'report.entintados_pdv.branch_cash_cut_detail_pdf'
    _inherit = 'report.entintados_pdv.branch_cash_cut_pdf'
    _description = 'Corte de caja detallado imprimible'
