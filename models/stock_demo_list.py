from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class StockPickingDemoList(models.Model):
    _inherit = 'stock.picking'

    demo_auditor_ids = fields.Many2many(
        related='approval_request_id.material_auditor_ids', string='Auditores asignados', readonly=True)
    demo_relations = fields.Json(string='Relaciones', compute='_compute_demo_relations')
    demo_crm_relation = fields.Json(string='CRM', compute='_compute_demo_relations')
    demo_approval_relation = fields.Json(string='Aprobaciones', compute='_compute_demo_relations')
    demo_salesperson_id = fields.Many2one(
        'res.users', string='Vendedor CRM', compute='_compute_demo_salesperson')

    @api.depends('crm_lead_id.user_id', 'approval_request_id.crm_lead_id.user_id')
    def _compute_demo_salesperson(self):
        for picking in self:
            lead = picking.crm_lead_id or picking.approval_request_id.crm_lead_id
            picking.demo_salesperson_id = lead.user_id

    def _demo_relation_record(self, relation):
        self.ensure_one()
        if relation == 'opportunity':
            return self.crm_lead_id or self.approval_request_id.crm_lead_id
        if relation == 'approval':
            return self.approval_request_id
        raise UserError(_('Relación de demostración no válida.'))

    @api.depends_context('uid')
    @api.depends('crm_lead_id', 'approval_request_id', 'approval_request_id.crm_lead_id')
    def _compute_demo_relations(self):
        for picking in self:
            counts = {}
            for key in ('opportunity', 'approval'):
                try:
                    record = picking._demo_relation_record(key)
                    if record:
                        record.check_access('read')
                    counts[key] = int(bool(record))
                except AccessError:
                    counts[key] = 0
            picking.demo_relations = counts
            picking.demo_crm_relation = {'opportunity': counts['opportunity']}
            picking.demo_approval_relation = {'approval': counts['approval']}

    def action_open_demo_relation(self, relation):
        self.ensure_one()
        self.check_access('read')
        record = self._demo_relation_record(relation)
        if not record:
            raise UserError(_('Esta salida no tiene el documento relacionado.'))
        record.check_access('read')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Oportunidad') if relation == 'opportunity' else _('Aprobación'),
            'res_model': record._name, 'res_id': record.id,
            'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current',
        }
