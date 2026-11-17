from odoo import fields, models, _
from odoo.fields import Domain


class AccountReport(models.Model):
    _inherit = 'account.report'

    def _init_options_buttons(self, options, previous_options):
        super()._init_options_buttons(options, previous_options)
        if self.custom_handler_model_name == 'account.customer.advance.report.handler':
            for button in options['buttons']:
                if button.get('action_param') == 'export_to_pdf':
                    button.update(action='action_open_advance_report_pdf')
                    button.pop('action_param')
                    button.pop('file_export_type')

    def action_open_advance_report_pdf(self, options, action_param=None):
        self.ensure_one()
        if self != self.env.ref('entintados_pdv.customer_advance_preview_report'):
            return False
        handler = self.env['account.customer.advance.report.handler']
        domain = handler._invoice_domain(self, self.get_options(previous_options=options))
        return self.env['account.move'].with_context(active_domain=list(domain)).action_open_customer_advance_print()


class CustomerAdvanceReportHandler(models.AbstractModel):
    _name = 'account.customer.advance.report.handler'
    _inherit = 'account.report.custom.handler'
    _description = 'Vista previa contable de facturas de anticipo'

    _balance_columns = ('amount_total', 'advance_available', 'advance_applied')
    _expand_function = '_report_expand_unfoldable_line_customer_advance'

    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options)
        options['custom_display_config'].setdefault('templates', {}).update({
            'AccountReportLineCell': 'entintados_pdv.CustomerAdvanceLineCell',
            'AccountReportLineName': 'entintados_pdv.CustomerAdvanceLineName',
        })

    def _invoice_domain(self, report, options):
        domain = Domain([
            ('move_type', '=', 'out_invoice'),
            ('is_advance_invoice', '=', True),
            ('company_id', 'in', report.get_report_company_ids(options)),
            ('invoice_date', '<=', options['date']['date_to']),
        ])
        if options['date'].get('date_from'):
            domain &= Domain('invoice_date', '>=', options['date']['date_from'])
        if options.get('partner_ids'):
            domain &= Domain('partner_id', 'in', options['partner_ids'])
        if options.get('partner_categories'):
            domain &= Domain('partner_id.category_id', 'in', options['partner_categories'])
        return domain

    def _columns(self, report, options, values):
        return [
            report._build_column_dict(values.get(column['expression_label']), column, options=options)
            for column in options['columns']
        ]

    def _convert(self, invoice, amount):
        company = self.env.company
        return invoice.currency_id._convert(
            amount, company.currency_id, company, fields.Date.context_today(self), round=False,
        )

    def _balances(self, invoices):
        return {
            name: sum(self._convert(invoice, invoice[name]) for invoice in invoices)
            for name in self._balance_columns
        }

    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals, warnings=None):
        invoices = self.env['account.move'].search(self._invoice_domain(report, options), order='partner_id, id')
        totals = dict.fromkeys(self._balance_columns, 0.0)
        lines = []
        for partner, moves in invoices.grouped('partner_id').items():
            line_id = report._get_generic_line_id('res.partner', partner.id)
            balances = self._balances(moves)
            for name, amount in balances.items():
                totals[name] += amount
            lines.append((0, {
                'id': line_id,
                'name': partner.display_name or _('Sin cliente'),
                'level': 1,
                'columns': self._columns(report, options, balances),
                'unfoldable': True,
                'unfolded': options['unfold_all'] or line_id in options['unfolded_lines'],
                'expand_function': self._expand_function,
            }))
        lines.append((0, {
            'id': report._get_generic_line_id(None, None, markup='total'),
            'name': _('Total general'),
            'level': 1,
            'columns': self._columns(report, options, totals),
        }))
        return lines

    def _report_expand_unfoldable_line_customer_advance(
        self, line_dict_id, groupby, options, progress, offset, unfold_all_batch_data=None,
    ):
        report = self.env['account.report'].browse(options['report_id'])
        model_name, record_id = report._get_model_info_from_id(line_dict_id)
        domain = self._invoice_domain(report, options)
        move_model = self.env['account.move']
        lines = []
        limit = report.load_more_limit if not options.get('export_mode') else 0
        if model_name == 'res.partner':
            invoices = move_model.search(
                domain & Domain('partner_id', '=', record_id or False),
                order='invoice_date desc, id desc', offset=offset, limit=limit + 1 if limit else None,
            )
            for invoice in invoices[:limit] if limit else invoices:
                line_id = report._get_generic_line_id('account.move', invoice.id, parent_line_id=line_dict_id)
                has_applications = bool(invoice.advance_used_invoice_ids)
                lines.append({
                    'id': line_id,
                    'parent_id': line_dict_id,
                    'name': invoice.name,
                    'caret_options': 'account.move',
                    'advance_name_color': 'text-danger' if invoice.currency_id.is_zero(invoice.advance_available) else 'text-warning',
                    'level': 2,
                    'columns': self._columns(report, options, {'invoice_date': invoice.invoice_date, **self._balances(invoice)}),
                    'unfoldable': has_applications,
                    'unfolded': has_applications and (options['unfold_all'] or line_id in options['unfolded_lines']),
                    'expand_function': self._expand_function,
                })
            return {'lines': lines, 'offset_increment': len(lines), 'has_more': bool(limit and len(invoices) > limit)}

        if model_name == 'account.move':
            advance = move_model.search(domain & Domain('id', '=', record_id), limit=1)
            if advance:
                amounts = advance._advance_used_amounts_by_invoice()
                for invoice in advance.advance_used_invoice_ids:
                    lines.append({
                        'id': report._get_generic_line_id('account.move', invoice.id, parent_line_id=line_dict_id),
                        'parent_id': line_dict_id,
                        'name': _('Aplicado en %s', invoice.name),
                        'caret_options': 'account.move',
                        'level': 3,
                        'columns': self._columns(report, options, {
                            'invoice_date': invoice.invoice_date,
                            'advance_applied': self._convert(advance, amounts.get(invoice.id, 0.0)),
                        }),
                        'unfoldable': False,
                        'unfolded': False,
                    })
        return {'lines': lines, 'offset_increment': len(lines), 'has_more': False}
