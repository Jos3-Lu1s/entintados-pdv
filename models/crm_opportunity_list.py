from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    opportunity_reference = fields.Char(
        string='Folio', readonly=True, copy=False, index=True)
    opportunity_relations = fields.Json(
        string='Aprobaciones', compute='_compute_opportunity_relations')

    @api.model
    def _next_opportunity_reference(self):
        reference = self.env['ir.sequence'].next_by_code('entintados.crm.opportunity')
        if not reference:
            raise UserError(_('No está configurada la secuencia de oportunidades. Actualiza Entintados PDV.'))
        return reference

    @api.model_create_multi
    def create(self, vals_list):
        default_type = self.default_get(['type']).get('type', 'lead')
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if vals.get('type', default_type) == 'opportunity':
                vals['opportunity_reference'] = self._next_opportunity_reference()
        return super().create(vals_list)

    def write(self, vals):
        result = super().write(vals)
        if vals.get('type') == 'opportunity':
            self._assign_opportunity_references()
        return result

    def _assign_opportunity_references(self):
        for lead in self.filtered(lambda lead: lead.type == 'opportunity' and not lead.opportunity_reference):
            lead.write({'opportunity_reference': self._next_opportunity_reference()})

    def _opportunity_related_records(self):
        self.ensure_one()
        access = self.env['ir.model.access']

        def search(model_name, domain):
            model = self.env[model_name]
            if not access.check(model_name, 'read', raise_exception=False):
                return model.browse()
            return model.search(domain)

        approvals = search('approval.request', [
            '|', ('crm_lead_id', '=', self.id), ('id', '=', self.approval_request_id.id),
        ])
        pickings = search('stock.picking', [
            '|', '|', ('crm_lead_id', '=', self.id),
            ('approval_request_id', 'in', approvals.ids),
            ('id', 'in', approvals.generated_picking_id.ids),
        ])
        return {
            'approvals': approvals,
            'quotations': search('sale.order', [
                ('opportunity_id', '=', self.id), ('state', 'in', ('draft', 'sent'))]),
            'activities': search('mail.activity', [('res_model', '=', 'crm.lead'), ('res_id', '=', self.id)]),
            'pickings': pickings,
        }

    @api.depends_context('uid')
    @api.depends('activity_ids', 'order_ids.state', 'demo_approval_ids', 'approval_request_id',
                 'picking_ids', 'demo_approval_ids.picking_ids', 'demo_approval_ids.generated_picking_id')
    def _compute_opportunity_relations(self):
        for lead in self:
            lead.opportunity_relations = {
                key: len(records) for key, records in lead._opportunity_related_records().items()
            }

    def action_open_opportunity_relation(self, relation):
        self.ensure_one()
        self.check_access('read')
        titles = {
            'approvals': _('Aprobaciones'), 'quotations': _('Cotizaciones'),
            'activities': _('Actividades pendientes'), 'pickings': _('Salidas de material'),
        }
        if relation not in titles:
            raise UserError(_('Relación de oportunidad no válida.'))
        records = self._opportunity_related_records()[relation]
        action = {
            'type': 'ir.actions.act_window', 'name': titles[relation],
            'res_model': records._name, 'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', records.ids)], 'target': 'current',
        }
        if len(records) == 1:
            action.update(view_mode='form', views=[(False, 'form')], res_id=records.id)
        return action
