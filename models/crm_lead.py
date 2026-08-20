from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

CREATE_QUOTATION_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_create_quotation'
CONFIRM_QUOTATION_ACTIVITY_XMLID = 'entintados_pdv.mail_activity_type_confirm_quotation'

class CrmLead(models.Model):
    _inherit = 'crm.lead'

    stage_type = fields.Selection(
        related='stage_id.stage_type',
        string='Tipo de Etapa',
        store=True,
    )
    
    draft_quotation_count = fields.Integer(
        compute='_compute_draft_quotation_count',
        string='Cotizaciones en Borrador'
    )

    def _prepare_customer_values(self, partner_name, is_company=False, parent_id=False):
        res = super()._prepare_customer_values(partner_name, is_company=is_company, parent_id=parent_id)
        res['is_customer'] = True
        return res
    
    def write(self, vals):
        if 'stage_id' in vals and not self.env.context.get('skip_stage_sequence_check'):
            new_stage = self.env['crm.stage'].browse(vals['stage_id'])
            for record in self:
                old_stage = record.stage_id
                if not old_stage or not new_stage or old_stage.id == new_stage.id:
                    continue

                if new_stage.stage_type in ('quotation', 'closed'):
                    raise ValidationError(_(
                        'No puedes mover manualmente la oportunidad a "%s". '
                        'Esta etapa se asigna automáticamente por el flujo de ventas.'
                    ) % new_stage.name)

                ordered_stages = self.env['crm.stage'].search([], order='sequence, id')
                stage_ids = ordered_stages.ids

                if old_stage.id not in stage_ids or new_stage.id not in stage_ids:
                    continue

                old_index = stage_ids.index(old_stage.id)
                new_index = stage_ids.index(new_stage.id)

                quotation_stage = ordered_stages.filtered(lambda s: s.stage_type == 'quotation')
                quotation_index = stage_ids.index(quotation_stage.id) if quotation_stage else None

                if quotation_index is not None and old_index < quotation_index and new_index < quotation_index:
                    continue

                if new_index > old_index + 1:
                    skipped = ordered_stages[old_index + 1:new_index]
                    raise ValidationError(_(
                        'No puedes saltar etapas. Antes de pasar a "%s" '
                        'debes pasar por: %s'
                    ) % (new_stage.name, ', '.join(skipped.mapped('name'))))

                if quotation_index is not None and old_index >= quotation_index and new_index < old_index:
                    raise ValidationError(_(
                        'No puedes regresar de "%s" a una etapa anterior una vez que '
                        'la oportunidad llegó a Cotización.'
                    ) % old_stage.name)

        return super().write(vals)
    
    @api.constrains('stage_id', 'expected_revenue')
    def _check_expected_revenue_in_quotation(self):
        for lead in self:
            if lead.stage_id.stage_type == 'quotation' and not lead.expected_revenue:
                raise ValidationError(_(
                'No puedes mover la oportunidad a la etapa "%s" sin un '
                'ingreso esperado (importe de la cotización) mayor a cero.'
            ) % lead.stage_id.name)
            
    @api.depends('order_ids.state')
    def _compute_draft_quotation_count(self):
        for lead in self:
            lead.draft_quotation_count = len(lead.order_ids.filtered(lambda o: o.state == 'draft'))
            
    def action_view_sale_quotation(self):
        action = super().action_view_sale_quotation()
        domain = action.get('domain', [])
        if isinstance(domain, list):
            domain = domain + [('state', '!=', 'cancel')]
        action['domain'] = domain
        return action
    
    def _schedule_create_quotation_activity(self, order):
        """Asigna al vendedor de la oportunidad una actividad al crearse una cotización."""
        self.ensure_one()
        activity_type = self.env.ref(CREATE_QUOTATION_ACTIVITY_XMLID, raise_if_not_found=False)
        if not activity_type:
            return
        # Evita duplicar la actividad si ya hay una abierta de este tipo
        already_open = self.activity_ids.filtered(
            lambda a: a.activity_type_id.id == activity_type.id
        )
        if already_open:
            return
        self.activity_schedule(
            activity_type_id=activity_type.id,
            user_id=self.user_id.id or self.env.uid,
            summary=activity_type.summary,
            note=_('Se creó la cotización %s.') % order.name,
        )
        
    def _schedule_confirm_quotation_activity(self, order):
        """Marca hecha la actividad de creación y asigna la de confirmación al vendedor."""
        self.ensure_one()
        
        create_activity_type = self.env.ref(CREATE_QUOTATION_ACTIVITY_XMLID, raise_if_not_found=False)
        if create_activity_type:
            pending = self.activity_ids.filtered(
                lambda a: a.activity_type_id.id == create_activity_type.id
            )
            if pending:
                pending.action_feedback(feedback=_('Cotización %s confirmada.') % order.name)

        confirm_activity_type = self.env.ref(CONFIRM_QUOTATION_ACTIVITY_XMLID, raise_if_not_found=False)
        if not confirm_activity_type:
            return
        # Evita duplicar la actividad si ya hay una abierta de este tipo
        already_open = self.activity_ids.filtered(
            lambda a: a.activity_type_id.id == confirm_activity_type.id
        )
        if already_open:
            return
        self.activity_schedule(
            activity_type_id=confirm_activity_type.id,
            user_id=self.user_id.id or self.env.uid,
            summary=confirm_activity_type.summary,
            note=_('Se confirmó la orden de venta %s.') % order.name,
        )

class CrmStage(models.Model):
    _inherit = 'crm.stage'

    stage_type = fields.Selection([
        ('new', 'Prospecto'),
        ('visit', 'Visita de campo'),
        ('demo', 'Demo'),
        ('quotation', 'Cotizacion'),
        ('closed', 'Venta Cerrada'),
        ('postsale', 'Post Venta')
        ])
    
    @api.constrains('stage_type')
    def _check_stage_type_unique(self):
        for record in self:
            if record.stage_type:
                duplicate = self.search([
                    ('stage_type', '=', record.stage_type),
                    ('id', '!=', record.id)
                ], limit=1)
                if duplicate:
                    raise ValidationError((
                        'El tipo "%s" ya está asignado a la etapa "%s". '
                        'Cada tipo solo puede usarse en una etapa.'
                    ) % (dict(record._fields['stage_type'].selection).get(record.stage_type), duplicate.name))