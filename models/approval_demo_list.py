from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class ApprovalDemoList(models.Model):
    _inherit = 'approval.request'

    demo_assigned_user_ids = fields.Many2many(
        'res.users', string='Asignado a', compute='_compute_demo_assigned_users')
    demo_salesperson_id = fields.Many2one(
        related='crm_lead_id.user_id', string='Vendedor CRM', readonly=True)
    demo_relations = fields.Json(string='Relaciones', compute='_compute_demo_relations')

    @api.depends('approver_ids.user_id')
    def _compute_demo_assigned_users(self):
        for request in self:
            request.demo_assigned_user_ids = request.approver_ids.user_id

    def _demo_related_records(self, relation):
        self.ensure_one()
        if relation == 'opportunity':
            records = self.crm_lead_id
        elif relation == 'pickings':
            records = self.picking_ids | self.generated_picking_id
        else:
            raise UserError(_('Relación de aprobación no válida.'))
        readable = records.browse()
        for record in records:
            try:
                record.check_access('read')
                readable |= record
            except AccessError:
                continue
        return readable

    @api.depends_context('uid')
    @api.depends('crm_lead_id', 'picking_ids', 'generated_picking_id')
    def _compute_demo_relations(self):
        for request in self:
            request.demo_relations = {
                key: len(request._demo_related_records(key))
                for key in ('opportunity', 'pickings')
            }

    def action_open_approval_demo_relation(self, relation):
        self.ensure_one()
        self.check_access('read')
        records = self._demo_related_records(relation)
        if not records:
            raise UserError(_('No hay documentos relacionados disponibles para tu usuario.'))
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Oportunidad') if relation == 'opportunity' else _('Salidas de material'),
            'res_model': records._name, 'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', records.ids)], 'target': 'current',
        }
        if len(records) == 1:
            action.update(res_id=records.id, view_mode='form', views=[(False, 'form')])
        return action
