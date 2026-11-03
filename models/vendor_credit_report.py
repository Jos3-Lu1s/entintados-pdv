import json
import math
from io import BytesIO
from urllib.parse import urlencode, quote

import xlsxwriter

from odoo import api, models, _
from odoo.exceptions import UserError


class VendorCreditGroupReportActions(models.Model):
    _inherit = 'vendor.credit.group'

    def action_export_distributed_credits_xlsx(self, all_records=False):
        self.check_access('read')
        if not all_records and not self:
            raise UserError(_('Selecciona al menos una nota distribuida.'))
        query = {'all': '1'} if all_records else {'ids': ','.join(map(str, self.ids))}
        return {'type': 'ir.actions.act_url', 'target': 'self',
                'url': '/entintados_pdv/distributed-credits/xlsx?' + urlencode(query)}

    def action_preview_distributed_credit(self):
        self.check_access('read')
        if len(self) == 1:
            filename = (self.name or str(self.id)).replace('/', '-').replace('\\', '-') + '.pdf'
            return {'type': 'ir.actions.act_url', 'target': 'new',
                    'url': '/entintados_pdv/distributed-credits/pdf/%s/%s' % (self.id, quote(filename, safe=''))}
        return {
            'type': 'ir.actions.act_url', 'target': 'new',
            'url': '/report/pdf/entintados_pdv.distributed_credit_report/%s' % ','.join(map(str, self.ids)),
        }

    def action_preview_all_distributed_credits(self):
        self.check_access('read')
        options = urlencode({'options': json.dumps({'summary_only': True, 'all_distributed': True})})
        return {'type': 'ir.actions.act_url', 'target': 'new',
                'url': '/report/pdf/entintados_pdv.distributed_credit_report?' + options}

    def action_download_distributed_credit(self):
        self.check_access('read')
        return self.env.ref('entintados_pdv.action_report_distributed_credit').report_action(self)

    def action_download_all_distributed_credits(self):
        # Record rules restrict companies and documents; never elevate report access.
        groups = self.search([], order='company_id, currency_id, date desc, id desc')
        if not groups:
            raise UserError(_('No hay notas distribuidas para descargar.'))
        return self.env.ref('entintados_pdv.action_report_distributed_credit').report_action(
            groups, data={'summary_only': True, 'doc_ids': groups.ids})


class DistributedCreditReport(models.AbstractModel):
    _name = 'report.entintados_pdv.distributed_credit_report'
    _description = 'Reporte de notas de crédito distribuidas'

    @api.model
    def _build_xlsx(self, docids, data=None):
        values = self._get_report_values(docids, data)
        if not values['docs']:
            raise UserError(_('No hay notas distribuidas para exportar.'))
        output = BytesIO()
        book = xlsxwriter.Workbook(output, {'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False})
        title = book.add_format({'bold': True, 'font_size': 16, 'font_color': '#16324F'})
        band = book.add_format({'bold': True, 'bg_color': '#EDF2F7', 'font_color': '#16324F', 'text_wrap': True})
        text = book.add_format({'valign': 'top', 'text_wrap': True})
        date = book.add_format({'num_format': 'dd/mm/yyyy', 'valign': 'top'})
        number = book.add_format({'num_format': '#,##0.00;[Red](#,##0.00);0.00', 'valign': 'top'})
        total = book.add_format({'bold': True, 'bg_color': '#EDF2F7', 'num_format': '#,##0.00;[Red](#,##0.00);0.00'})
        general = book.add_worksheet('General')
        detail = book.add_worksheet('Distribucion')
        credits = book.add_worksheet('Notas generadas')
        sheets = (general, detail, credits)
        headers = (
            ['Folio', 'Fecha', 'Proveedor', 'Motivo', 'Estado', 'A distribuir', 'Contabilizado'],
            ['Agrupación', 'Factura de origen', 'Proveedor', 'Total factura', 'Importe distribuido', 'Notas generadas / estado'],
            ['Agrupación', 'Nota', 'Fecha', 'Estado', 'Subtotal', 'Impuestos', 'Total'],
        )
        positions = [3, 3, 3]
        for sheet, label in zip(sheets, ('Notas de crédito distribuidas', 'Distribución por factura', 'Notas de crédito generadas')):
            sheet.hide_gridlines(2)
            sheet.set_landscape()
            sheet.set_paper(9)
            sheet.fit_to_pages(1, 0)
            sheet.freeze_panes(5, 0)
            sheet.set_column(0, 1, 23)
            sheet.set_column(2, 3, 35)
            sheet.set_column(4, 6, 23)
            sheet.merge_range(0, 0, 0, 6, label, title)
            sheet.merge_range(1, 0, 1, 6, 'Importes con impuestos. Contabilizado excluye borradores y canceladas. Totales separados por compañía y moneda.', text)
            sheet.set_row(1, 32)
            sheet.set_footer('Página &P de &N')

        def write_row(sheet, row, cells):
            lines = max((sum(max(1, math.ceil(len(part) / (35 if column in (2, 3) else 23)))
                             for part in str(cell or '').split('\n'))
                         for column, cell in enumerate(cells) if isinstance(cell, str)), default=1)
            sheet.set_row(row, max(32, lines * 15))
            for column, cell in enumerate(cells):
                if cell is None or cell is False:
                    sheet.write_blank(row, column, None, text)
                elif isinstance(cell, (int, float)):
                    sheet.write_number(row, column, cell, number)
                elif hasattr(cell, 'year'):
                    sheet.write_datetime(row, column, cell, date)
                else:
                    sheet.write_string(row, column, str(cell or ''), text)

        for section in values['sections']:
            currency = section['currency']
            section_title = '%s (%s)' % (section['company'].name, currency.name)
            starts = []
            for index, sheet in enumerate(sheets):
                row = positions[index]
                sheet.merge_range(row, 0, row, len(headers[index]) - 1, section_title, band)
                sheet.write_row(row + 1, 0, headers[index], band)
                sheet.set_row(row + 1, 30)
                positions[index] += 2
                starts.append(positions[index])
            for item in section['items']:
                doc = item['doc']
                states = dict(doc._fields['state']._description_selection(self.env))
                write_row(general, positions[0], [doc.name, doc.date, doc.partner_id.name, doc.reason,
                    states.get(doc.state, doc.state), item['requested'], item['posted']])
                positions[0] += 1
                for row in item['rows']:
                    line = row['line']
                    linked = ', '.join('%s / %s' % (credit.name, dict(credit._fields['state']._description_selection(self.env)).get(credit.state, credit.state))
                                       for credit in row['credits']) or _('Sin nota vinculada')
                    write_row(detail, positions[1], [doc.name, line.invoice_id.name, doc.partner_id.name,
                        line.invoice_id.amount_total, line.amount, linked])
                    positions[1] += 1
                for credit in item['credits']:
                    state = dict(credit._fields['state']._description_selection(self.env)).get(credit.state, credit.state)
                    write_row(credits, positions[2], [doc.name, credit.name, credit.invoice_date, state,
                        credit.amount_untaxed, credit.amount_tax, credit.amount_total])
                    positions[2] += 1
            row = positions[0]
            general.merge_range(row, 0, row, 4, 'Total en ' + currency.name, band)
            for column, key in ((5, 'requested'), (6, 'posted')):
                letter = 'F' if column == 5 else 'G'
                general.write_formula(row, column, '=SUM(%s%d:%s%d)' % (letter, starts[0] + 1, letter, row), total, section[key])
            positions[0] += 1
            for index, sheet in enumerate(sheets):
                if positions[index] == starts[index]:
                    sheet.write_string(positions[index], 0, 'Sin documentos vinculados', text)
                    positions[index] += 1
                positions[index] += 2
        for sheet, end in zip(sheets, positions):
            sheet.print_area(0, 0, end - 1, 6)
        book.close()
        return output.getvalue()

    @api.model
    def _get_report_values(self, docids, data=None):
        model = self.env['vendor.credit.group']
        docs = (model.search([]) if (data or {}).get('all_distributed')
                else model.browse(docids or (data or {}).get('doc_ids', [])).exists())
        docs.check_access('read')
        sections = {}
        for doc in docs.sorted(lambda item: (item.company_id.id, item.currency_id.id, item.date, item.id)):
            doc.line_ids.check_access('read')
            doc.credit_ids.check_access('read')
            selected = doc.line_ids.filtered('selected')
            credits = doc.credit_ids.filtered(lambda move: move.state == 'posted')
            rows = []
            for line in selected:
                linked = line.credit_id or doc.credit_ids.filtered(
                    lambda move: line.invoice_id in move.vendor_credit_source_ids)
                rows.append({'line': line, 'credits': linked})
            values = {'doc': doc, 'rows': rows, 'credits': doc.credit_ids,
                      'requested': sum(selected.mapped('amount')),
                      'posted': sum(credits.mapped('amount_total')),
                      'tax': sum(credits.mapped('amount_tax')),
                      'untaxed': sum(credits.mapped('amount_untaxed'))}
            key = (doc.company_id.id, doc.currency_id.id)
            section = sections.setdefault(key, {'company': doc.company_id, 'currency': doc.currency_id,
                'items': [], 'requested': 0, 'posted': 0, 'count': 0})
            section['items'].append(values)
            section['requested'] += values['requested']
            section['posted'] += values['posted']
            section['count'] += len(credits)
        return {'doc_ids': docs.ids, 'doc_model': 'vendor.credit.group', 'docs': docs,
                'sections': list(sections.values()), 'summary_only': bool((data or {}).get('summary_only'))}
