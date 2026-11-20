from odoo import fields, models, _
from odoo.exceptions import AccessError
from odoo.fields import Domain


class SalesCollectionReportHandler(models.AbstractModel):
    _name = 'account.sales.collection.report.handler'
    _inherit = 'account.report.custom.handler'
    _description = 'Reporte de cobranza por vendedor'

    _expand_function = '_report_expand_unfoldable_line_sales_collection'

    def _check_report_access(self):
        if not self.env.user.has_group('entintados_pdv.group_sales_collection_own'):
            raise AccessError(_('No tienes permiso para consultar Cobranza por vendedor.'))

    def _can_view_all_sellers(self):
        return self.env.user.has_group('entintados_pdv.group_sales_collection_all_sellers')

    def _custom_options_initializer(self, report, options, previous_options):
        self._check_report_access()
        super()._custom_options_initializer(report, options, previous_options)
        can_view_all = self._can_view_all_sellers()
        options['sales_collection_can_view_all_sellers'] = can_view_all
        options['sales_collection_seller_ids'] = (
            previous_options.get('sales_collection_seller_ids') or [] if can_view_all else []
        )
        policy = previous_options.get('sales_collection_payment_policy')
        options['sales_collection_payment_policy'] = policy if policy in ('PPD', 'PUE') else ''
        options['custom_display_config'].setdefault('templates', {})['AccountReportFilters'] = (
            'entintados_pdv.SalesCollectionFilters'
        )

    def _invoice_domain(self, report, options):
        self._check_report_access()
        domain = Domain([
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('l10n_mx_edi_payment_policy', 'in', ('PPD', 'PUE')),
            ('company_id', 'in', report.get_report_company_ids(options)),
            ('invoice_date', '<=', options['date']['date_to']),
        ])
        if options.get('partner_ids'):
            domain &= Domain('partner_id', 'in', options['partner_ids'])
        if options.get('partner_categories'):
            domain &= Domain('partner_id.category_id', 'in', options['partner_categories'])
        if not self._can_view_all_sellers():
            domain &= Domain('invoice_user_id', '=', self.env.uid)
        elif options.get('sales_collection_seller_ids'):
            domain &= Domain('invoice_user_id', 'in', options['sales_collection_seller_ids'])
        if options.get('sales_collection_payment_policy'):
            domain &= Domain('l10n_mx_edi_payment_policy', '=', options['sales_collection_payment_policy'])
        return domain

    def _total(self, invoices):
        currency = self.env.company.currency_id
        return sum(
            invoice.company_currency_id._convert(
                invoice.amount_total_signed, currency, invoice.company_id, invoice.date, round=False,
            )
            for invoice in invoices
        )

    def _last_payment_date(self, invoice, cutoff):
        counterparts = invoice._get_reconciled_amls().move_id.filtered(lambda move: move.state == 'posted')
        dates = [
            move.origin_payment_id.date or move.statement_line_id.date or move.date
            for move in counterparts
            if move.origin_payment_id or move.statement_line_id
            or (move.move_type == 'entry' and move.journal_id.type in ('bank', 'cash', 'credit'))
        ]
        dates.extend(invoice.matched_payment_ids.filtered(
            lambda payment: payment.state == 'paid' and payment.payment_type == 'inbound'
        ).mapped('date'))
        # Accounting readers can see the invoice without having access to POS orders.
        pos_payments = invoice.sudo().pos_order_ids.filtered(
            lambda order: order.state in ('paid', 'done', 'invoiced')
        ).payment_ids
        dates.extend(
            fields.Date.context_today(self, payment.payment_date)
            for payment in pos_payments
            if payment.amount > 0 and payment.payment_date and payment.payment_method_id.type != 'pay_later'
        )
        return max((date for date in dates if date and date <= cutoff), default=None)

    def _commission_values(self, invoices, options, historical=False):
        cutoff = fields.Date.to_date(options['date']['date_to'])
        company = self.env.company
        currency = company.currency_id
        ranges = self.env['commission.range'].sudo().search([], order='margin_from, id')
        goals = self.env['commission.goal'].sudo().search([
            ('seller_id', 'in', invoices.invoice_user_id.ids),
        ], order='id')
        goals_by_seller = {}
        for goal in goals:
            goals_by_seller.setdefault(goal.seller_id.id, goal)

        values = {}
        for invoice in invoices:
            payment = self._last_payment_date(invoice, fields.Date.context_today(self))
            if (invoice.is_advance_invoice and not historical) or not invoice.invoice_user_id or invoice.payment_state != 'paid' or not payment or payment > cutoff:
                continue
            subtotal = invoice.currency_id._convert(
                invoice.amount_untaxed, currency, invoice.company_id, invoice.date, round=False,
            )
            cost = invoice.sudo()._historical_commission_cost() if historical else sum(
                line.product_id.uom_id._compute_price(line.product_id.standard_price, line.product_uom_id) * line.quantity
                for line in invoice.sudo().with_company(invoice.company_id).invoice_line_ids
                if line.display_type == 'product' and line.product_id and line.product_uom_id
            )
            if cost is None:
                if historical:
                    values[invoice.id] = {
                        'commission_payment_date': payment,
                        'commission_credit_days': (invoice.invoice_date_due - invoice.invoice_date).days if invoice.invoice_date_due and invoice.invoice_date else 0,
                        'commission_payment_days': (payment - invoice.invoice_date).days if invoice.invoice_date else 0,
                        'commission_delay': max((payment - invoice.invoice_date_due).days, 0) if invoice.invoice_date_due else 0,
                    }
                continue
            cost = invoice.company_currency_id._convert(cost, currency, invoice.company_id, invoice.date, round=False)
            margin = (subtotal - cost) / subtotal if subtotal else 0.0
            band = ranges.filtered(lambda row: row.margin_from <= margin <= row.margin_to)[:1]
            rate = band.percentage if band else 0.0
            before_delay = subtotal * rate if subtotal > 0 else 0.0
            delay = max((payment - invoice.invoice_date_due).days, 0) if invoice.invoice_date_due else 0
            values[invoice.id] = {
                'commission_base': subtotal,
                'commission_cost': cost,
                'commission_margin': margin * 100,
                'commission_rate': rate * 100,
                'commission_delay': delay,
                'commission_payment_date': payment,
                'commission_credit_days': (invoice.invoice_date_due - invoice.invoice_date).days if invoice.invoice_date_due and invoice.invoice_date else 0,
                'commission_payment_days': (payment - invoice.invoice_date).days if invoice.invoice_date else 0,
                'commission_before_delay': currency.round(before_delay),
                'commission_amount': currency.round(before_delay * max(1 - delay / 100, 0)),
            }
        seller_values = {}
        for seller, moves in invoices.grouped('invoice_user_id').items():
            goal = goals_by_seller.get(seller.id)
            if goal and goal.commercial_goal > 0:
                target = goal.currency_id._convert(goal.commercial_goal, currency, company, cutoff)
                seller_values[seller.id] = {
                    'commission_goal': target,
                    'commission_scope': sum(values.get(move.id, {}).get('commission_base', 0) for move in moves) / target * 100
                    if target else None,
                }
        return values, seller_values

    def _commission_sum(self, invoices, values):
        amounts = [values[invoice.id]['commission_amount'] for invoice in invoices if invoice.id in values]
        return {'commission_amount': sum(amounts)} if amounts else {}

    def _columns(self, report, options, values):
        return [
            report._build_column_dict(
                values.get(column['expression_label']), column, options=options,
                currency=self.env.company.currency_id,
            )
            for column in options['columns']
        ]

    def _group_line(self, report, options, record, invoices, level, parent_id=None, commission=None):
        line_id = report._get_generic_line_id(record._name, record.id, parent_line_id=parent_id)
        fallback = _('Sin vendedor') if level == 1 else _('Sin cliente')
        line = {
            'id': line_id,
            'name': record.display_name or fallback,
            'level': level,
            'columns': self._columns(report, options, {'total': self._total(invoices), **(commission or {})}),
            'unfoldable': True,
            'unfolded': options['unfold_all'] or line_id in options['unfolded_lines'],
            'expand_function': self._expand_function,
        }
        if parent_id:
            line['parent_id'] = parent_id
        return line

    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals, warnings=None):
        invoices = self.env['account.move'].search(self._invoice_domain(report, options), order='invoice_user_id, id')
        values, goals = self._commission_values(invoices, options)
        lines = [
            (0, self._group_line(report, options, seller, moves, 1, commission={
                **self._commission_sum(moves, values), **goals.get(seller.id, {}),
            }))
            for seller, moves in invoices.grouped('invoice_user_id').items()
        ]
        lines.append((0, {
            'id': report._get_generic_line_id(None, None, markup='total'),
            'name': _('Total general'),
            'level': 1,
            'columns': self._columns(report, options, {'total': self._total(invoices), **self._commission_sum(invoices, values)}),
        }))
        return lines

    def _report_expand_unfoldable_line_sales_collection(
        self, line_dict_id, groupby, options, progress, offset, unfold_all_batch_data=None,
    ):
        report = self.env['account.report'].browse(options['report_id'])
        domain = self._invoice_domain(report, options)
        model, record_id = report._get_model_info_from_id(line_dict_id)
        move_model = self.env['account.move']
        if model == 'res.users':
            invoices = move_model.search(
                domain & Domain('invoice_user_id', '=', record_id or False), order='partner_id, id',
            )
            values, _goals = self._commission_values(invoices, options)
            lines = [
                self._group_line(report, options, partner, moves, 2, line_dict_id, self._commission_sum(moves, values))
                for partner, moves in invoices.grouped('partner_id').items()
            ]
            return {'lines': lines, 'offset_increment': len(lines), 'has_more': False}

        if model != 'res.partner':
            return {'lines': [], 'offset_increment': 0, 'has_more': False}
        seller_line_id = line_dict_id.rsplit('|', 1)[0]
        seller_model, seller_id = report._get_model_info_from_id(seller_line_id)
        if seller_model != 'res.users':
            return {'lines': [], 'offset_increment': 0, 'has_more': False}
        domain &= Domain('invoice_user_id', '=', seller_id or False) & Domain('partner_id', '=', record_id or False)
        limit = report.load_more_limit if not options.get('export_mode') else 0
        invoices = move_model.search(domain, order='invoice_date desc, id desc', offset=offset, limit=limit + 1 if limit else None)
        values, _goals = self._commission_values(invoices, options)
        cutoff = fields.Date.to_date(options['date']['date_to'])
        lines = []
        for invoice in invoices[:limit] if limit else invoices:
            lines.append({
                'id': report._get_generic_line_id('account.move', invoice.id, parent_line_id=line_dict_id),
                'parent_id': line_dict_id,
                'name': f'{invoice.name} ({invoice.l10n_mx_edi_payment_policy})',
                'level': 3,
                'columns': self._columns(report, options, {
                    'invoice_date': invoice.invoice_date,
                    'last_payment': self._last_payment_date(invoice, cutoff),
                    'total': self._total(invoice),
                    **values.get(invoice.id, {}),
                }),
                'unfoldable': False,
                'unfolded': False,
            })
        return {'lines': lines, 'offset_increment': len(lines), 'has_more': bool(limit and len(invoices) > limit)}
