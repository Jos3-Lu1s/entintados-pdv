from werkzeug.exceptions import BadRequest, NotFound

from odoo import http
from odoo.http import request, content_disposition


class DistributedCreditExport(http.Controller):
    @http.route('/entintados_pdv/distributed-credits/pdf/<int:group_id>/<string:filename>',
                type='http', auth='user', methods=['GET'])
    def distributed_credit_pdf(self, group_id, filename, **params):
        group = request.env['vendor.credit.group'].browse(group_id).exists()
        if not group:
            raise NotFound()
        group.check_access('read')
        name = (group.name or str(group.id)).replace('/', '-').replace('\\', '-') + '.pdf'
        content, _ = request.env['ir.actions.report']._render_qweb_pdf(
            'entintados_pdv.distributed_credit_report', group.ids)
        return request.make_response(content, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', content_disposition(name).replace('attachment;', 'inline;', 1)),
            ('Content-Length', str(len(content))),
            ('Cache-Control', 'no-store'),
        ])

    @http.route('/entintados_pdv/distributed-credits/xlsx', type='http', auth='user', methods=['GET'])
    def distributed_credit_xlsx(self, ids='', **params):
        try:
            docids = [int(value) for value in ids.split(',') if value]
        except ValueError:
            raise BadRequest('Identificadores de notas inválidos') from None
        content = request.env['report.entintados_pdv.distributed_credit_report']._build_xlsx(
            docids, {'all_distributed': params.get('all') == '1'})
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition('Notas distribuidas.xlsx')),
            ('Content-Length', str(len(content))),
            ('Cache-Control', 'no-store'),
        ])
