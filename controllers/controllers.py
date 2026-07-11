# -*- coding: utf-8 -*-
# from odoo import http


# class EntintadosPdv(http.Controller):
#     @http.route('/entintados_pdv/entintados_pdv', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/entintados_pdv/entintados_pdv/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('entintados_pdv.listing', {
#             'root': '/entintados_pdv/entintados_pdv',
#             'objects': http.request.env['entintados_pdv.entintados_pdv'].search([]),
#         })

#     @http.route('/entintados_pdv/entintados_pdv/objects/<model("entintados_pdv.entintados_pdv"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('entintados_pdv.object', {
#             'object': obj
#         })

