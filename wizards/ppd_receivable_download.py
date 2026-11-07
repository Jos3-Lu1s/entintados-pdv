from odoo import fields, models, _
from odoo.exceptions import UserError


class PpdReceivableDownload(models.TransientModel):
    _name = 'ppd.receivable.download'
    _description = 'Descargar cuentas por cobrar PPD por cliente'

    partner_id = fields.Many2one('res.partner', string='Cliente', required=True,
                                domain="[('commercial_partner_id.is_credit', '=', True)]")
    date_to = fields.Date(string='Fecha de corte', required=True, readonly=True,
                          default=fields.Date.context_today)
    report_options = fields.Json(default=dict, readonly=True)

    def _get_report_data(self):
        self.ensure_one()
        self.check_access('read')
        partner = self.partner_id.commercial_partner_id
        if not partner or not partner.is_credit:
            raise UserError(_('Selecciona un cliente con condición de crédito.'))
        report = self.env.ref('entintados_pdv.ppd_receivable_report')
        previous = {**(self.report_options or {}), 'selected_variant_id': report.id,
                    'is_opening_report': False, 'partner_ids': [], 'partner_categories': [],
                    'date': {'date_from': str(self.date_to), 'date_to': str(self.date_to),
                             'mode': 'single', 'filter': 'custom'},
                    'forced_domain': [('move_id.commercial_partner_id', '=', partner.id)]}
        options = report.get_options(previous)
        options['forced_domain'] = [('move_id.commercial_partner_id', '=', partner.id)]
        self.env.flush_all()
        report._init_currency_table(options)
        handler = self.env['account.ppd.receivable.report.handler']
        # The native engine handles historical partial reconciliations and currency conversion.
        results = handler._aged_partner_report_custom_engine_common(
            options, 'asset_receivable', 'id', None,
        )
        amounts = {aml_id: sum(value for label, value in values.items()
                               if label.startswith('period')) for aml_id, values in results}
        currency = self.env.company.currency_id
        rows = []
        for aml in self.env['account.move.line'].browse(list(amounts)):
            if currency.is_zero(amounts[aml.id]):
                continue
            due = aml.date_maturity or aml.move_id.invoice_date_due or aml.date
            rows.append({'customer': partner.display_name, 'invoice': aml.move_id.name,
                         'invoice_date': aml.move_id.invoice_date, 'due_date': due,
                         'days': max((self.date_to - due).days, 0), 'amount': amounts[aml.id]})
        rows.sort(key=lambda row: (row['invoice_date'] or self.date_to, row['invoice'], row['due_date']))
        companies = self.env['res.company'].browse(report.get_report_company_ids(options))
        limit = sum((partner.with_company(company).risk_currency_id or company.currency_id)._convert(
            partner.with_company(company).credit_limit, currency, company, self.date_to,
        ) for company in companies)
        return {'customer': partner.display_name, 'date_to': self.date_to, 'currency': currency,
                'companies': ', '.join(companies.mapped('name')), 'credit_limit': limit,
                'rows': rows, 'total': sum(row['amount'] for row in rows)}

    def action_download(self):
        self.ensure_one()
        if not self._get_report_data()['rows']:
            raise UserError(_('El cliente no tiene facturas PPD pendientes en la fecha de corte seleccionada.'))
        return self.env.ref('entintados_pdv.action_ppd_customer_pdf').report_action(self, config=False)
