import json
from io import BytesIO

import xlsxwriter
from werkzeug.exceptions import BadRequest

from odoo import fields, http
from odoo.http import content_disposition, request


class TintSalesExport(http.Controller):
    def _filtered_rows(self, domain, companies, report_model='tint.sales.report'):
        if report_model not in ('tint.sales.report', 'tint.category.sales.report'):
            raise BadRequest('Reporte inválido')
        try:
            domain = json.loads(domain)
            companies = json.loads(companies)
            if not isinstance(domain, list) or not isinstance(companies, list):
                raise ValueError
        except ValueError:
            raise BadRequest('Filtros del reporte inválidos') from None

        report = request.env[report_model]
        if companies:
            report = report.with_context(allowed_company_ids=companies)
        return report.search(domain)

    @http.route('/entintados_pdv/tint-sales/pdf', type='http', auth='user', methods=['GET', 'POST'])
    def download_tint_sales_pdf(self, domain='[]', companies='[]', show_margin='0', report_model='tint.sales.report'):
        rows = self._filtered_rows(domain, companies, report_model)
        action = ('entintados_pdv.action_tint_category_sales_pdf' if report_model == 'tint.category.sales.report'
                  else 'entintados_pdv.action_tint_sales_pdf')
        content, _ = rows.env['ir.actions.report'].with_context(tint_show_margin=show_margin == '1')._render_qweb_pdf(
            action, res_ids=rows.ids)
        return request.make_response(content, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', content_disposition('Reporte de entintados.pdf').replace('attachment;', 'inline;', 1)),
            ('Cache-Control', 'no-store'),
        ])

    @http.route('/entintados_pdv/tint-sales/xlsx', type='http', auth='user', methods=['GET', 'POST'])
    def download_tint_sales(self, domain='[]', companies='[]', show_margin='0', report_model='tint.sales.report'):
        rows = self._filtered_rows(domain, companies, report_model)
        by_category = report_model == 'tint.category.sales.report'
        include_margin = show_margin == '1'
        amount_column = 9 if include_margin else 8
        quantity_column = amount_column + 2
        output = BytesIO()
        statuses = dict(rows._fields['status']._description_selection(rows.env))
        with xlsxwriter.Workbook(output, {
            'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False,
        }) as workbook:
            sheet = workbook.add_worksheet('Entintados')
            sheet.set_landscape()
            sheet.fit_to_pages(1, 0)
            sheet.freeze_panes(1, 0)
            sheet.repeat_rows(0)
            header = workbook.add_format({'bold': True, 'bg_color': '#E4EAF4', 'border': 1,
                                          'text_wrap': True, 'valign': 'vcenter'})
            body = workbook.add_format({'border': 1, 'text_wrap': True, 'valign': 'top'})
            link = workbook.add_format({'border': 1, 'text_wrap': True, 'valign': 'top',
                                        'font_color': '#0563C1', 'underline': 1})
            date_format = workbook.add_format({'num_format': 'dd/mm/yyyy hh:mm', 'border': 1,
                                               'valign': 'top'})
            money_formats = {}
            labels = ['Fecha', 'Documento', 'Origen', 'Base + color', 'Color',
                      'Tipo de base', 'Estado', 'Moneda']
            widths = [20, 23, 17, 48, 32, 18, 18, 10]
            if include_margin:
                labels.append('Margen')
                widths.append(19)
            labels += ['Importe', 'Total', 'Entintados']
            widths += [24, 24, 13]
            if by_category:
                labels.append('Categoría de entintado')
                widths.append(26)
            sheet.write_row(0, 0, labels, header)
            sheet.set_row(0, 32)
            for index, width in enumerate(widths):
                sheet.set_column(index, index, width)
            for index, row in enumerate(rows, 1):
                sheet.set_row(index, 48)
                for column in range(len(labels)):
                    sheet.write_blank(index, column, None, body)
                if row.date_order:
                    local = fields.Datetime.context_timestamp(row, row.date_order)
                    sheet.write_datetime(index, 0, local.replace(tzinfo=None), date_format)
                sheet.write_row(index, 1, [
                    row.reference or '', 'Ventas' if row.source == 'sale' else 'Punto de venta',
                    row.base_color or '', row.color or '', row.base_type_id.display_name or '',
                    statuses[row.status], row.currency_id.name,
                ], body)
                if row.document_url:
                    sheet.write_url(index, 1, row.document_url, link, row.reference or '')
                sheet.write_number(index, quantity_column, row.quantity, body)
                if by_category:
                    sheet.write(index, quantity_column + 1, row.tint_category_id.display_name or '', body)
                digits = row.currency_id.decimal_places
                if digits not in money_formats:
                    pattern = '#,##0' + ('.' + '0' * digits if digits else '')
                    money_formats[digits] = workbook.add_format({'num_format': pattern, 'border': 1,
                                                                'valign': 'top'})
                money = money_formats[digits]
                if row.invoiced:
                    sheet.write_number(index, amount_column + 1, row.invoiced_amount, money)
                sheet.write_number(index, amount_column, row.sale_price, money)
                if include_margin and row.has_margin:
                    sheet.write_number(index, 8, row.margin, money)
            sheet.autofilter(0, 0, len(rows), len(labels) - 1)
            sheet.write(len(rows) + 1, quantity_column - 1, 'Total de entintados', header)
            sheet.write_number(len(rows) + 1, quantity_column, sum(rows.mapped('quantity')), header)
        return request.make_response(output.getvalue(), headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition('Reporte de entintados.xlsx')),
            ('Cache-Control', 'no-store'),
        ])
