import json
import re
from io import BytesIO

import xlsxwriter
from werkzeug.exceptions import BadRequest, Forbidden

from odoo import http
from odoo.fields import Domain
from odoo.http import content_disposition, request
from odoo.tools.safe_eval import safe_eval


class CommissionExport(http.Controller):
    @http.route('/entintados_pdv/commissions/xlsx', type='http', auth='user', methods=['GET'])
    def download_commissions(self, domain='[]'):
        env = request.env
        if not env.user.has_group('account.group_account_manager'):
            raise Forbidden()
        action = env.ref('entintados_pdv.action_invoice_commissions')
        try:
            filters = Domain(json.loads(domain))
        except (TypeError, ValueError):
            raise BadRequest('Filtro inválido') from None
        invoices = env['account.move'].search(
            Domain(safe_eval(action.domain)) & filters, order='invoice_date desc, id desc',
        )
        output = BytesIO()
        with xlsxwriter.Workbook(output, {'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False}) as book:
            header = book.add_format({'bold': True, 'bg_color': '#DCE8F5'})
            date_fmt = book.add_format({'num_format': 'dd/mm/yyyy'})
            percent = book.add_format({'num_format': '0.00%'})
            money = book.add_format({'num_format': '#,##0.00'})
            names = set()
            columns = [
                'Fecha', 'Fecha último pago', 'Factura', 'Cliente', 'Vendedor', 'Total', 'Subtotal',
                'Costo', 'Margen', '% comisión', 'Previo cobranza',
                'Días de crédito', 'Días de cobro', 'Demora cobranza', 'Comisión',
                'Moneda factura', 'Moneda comisión',
            ]
            for seller, records in invoices.grouped('invoice_user_id').items():
                base = re.sub(r"[\[\]:*?/\\]", '-', seller.name).strip("' ") or 'Vendedor'
                if base.lower() == 'history':
                    base = 'History vendedor'
                name = base[:31].rstrip("'")
                suffix = 2
                while name.casefold() in names:
                    ending = f' ({suffix})'
                    name = base[:31 - len(ending)] + ending
                    suffix += 1
                names.add(name.casefold())
                sheet = book.add_worksheet(name)
                sheet.write_row(0, 0, columns, header)
                sheet.freeze_panes(1, 0)
                sheet.set_column(0, 1, 18, date_fmt)
                sheet.set_column(2, 4, 28)
                sheet.set_column(5, 7, 17, money)
                sheet.set_column(8, 9, 25, percent)
                sheet.set_column(10, 10, 28, money)
                sheet.set_column(11, 13, 19)
                sheet.set_column(14, 14, 17, money)
                sheet.set_column(15, 16, 18)
                for row, invoice in enumerate(records, 1):
                    available = invoice.osel_commission_available
                    paid = invoice.osel_payment_date
                    sheet.write_row(row, 0, [
                        invoice.invoice_date or None, paid or None, invoice.name, invoice.partner_id.display_name,
                        seller.name, invoice.amount_total, invoice.amount_untaxed, invoice.osel_cost if available else None,
                        invoice.osel_commission_margin if available else None,
                        invoice.osel_commission_rate if available else None,
                        invoice.osel_before_delay if available else None, invoice.osel_credit_days_label or None,
                        invoice.osel_payment_days if paid else None, invoice.osel_collection_delay if paid else None,
                        invoice.osel_commission_amount if available else None, invoice.currency_id.name,
                        invoice.osel_commission_currency_id.name,
                    ])
                sheet.autofilter(0, 0, len(records), len(columns) - 1)
            if not names:
                book.add_worksheet('Comisiones').write('A1', 'Sin facturas para los filtros seleccionados.')
        return request.make_response(output.getvalue(), headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition('Comisiones por vendedor.xlsx')),
            ('Cache-Control', 'no-store'),
        ])
