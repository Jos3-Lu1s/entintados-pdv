from odoo import http
from odoo.http import content_disposition, request
from werkzeug.exceptions import BadRequest, NotFound


class VendorPaymentGroupReport(http.Controller):
    @http.route('/entintados_pdv/vendor-monthly-payments/<string:selection>/xlsx',
                type='http', auth='user', methods=['GET'])
    def download_monthly_xlsx(self, selection):
        try:
            ids = [int(value) for value in selection.split(',')]
        except ValueError:
            raise BadRequest() from None
        payments = request.env['account.payment'].browse(list(dict.fromkeys(ids))).exists()
        if not payments:
            raise NotFound()
        data = payments._monthly_payment_xlsx()
        return request.make_response(data, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition('Pagos mensuales a proveedores.xlsx')),
            ('Cache-Control', 'no-store'),
        ])

    def _report_groups(self, selection):
        model = request.env['vendor.payment.group']
        if selection == 'all':
            group = model.search([])
        else:
            try:
                ids = [int(value) for value in selection.split(',')]
            except ValueError:
                raise BadRequest() from None
            group = model.browse(list(dict.fromkeys(ids))).exists()
        if not group:
            raise NotFound()
        group.check_access('read')
        return group

    @http.route('/entintados_pdv/vendor-payment-group/<string:selection>/pdf', type='http', auth='user', methods=['GET'])
    def preview_pdf(self, selection):
        group = self._report_groups(selection)
        data, _ = request.env['ir.actions.report']._render_qweb_pdf(
            'entintados_pdv.vendor_payment_group_report', group.ids,
        )
        filename = group.name if len(group) == 1 else 'Pagos agrupados'
        disposition = content_disposition(f'{filename}.pdf').replace('attachment;', 'inline;', 1)
        return request.make_response(data, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', disposition),
            ('Cache-Control', 'no-store'),
        ])

    @http.route('/entintados_pdv/vendor-payment-group/<string:selection>/xlsx', type='http', auth='user', methods=['GET'])
    def download_xlsx(self, selection):
        group = self._report_groups(selection)
        data = group._payment_group_xlsx()
        filename = group.name if len(group) == 1 else 'Pagos agrupados'
        return request.make_response(data, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(f'{filename}.xlsx')),
        ])
