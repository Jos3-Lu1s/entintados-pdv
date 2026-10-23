"""Correo de asignación para las dos actividades del flujo de demostración."""
from odoo import Command, api, models


class DemoActivityMail(models.Model):
    _inherit = 'mail.activity'

    def _demo_notification_values(self):
        self.ensure_one()
        stage = False
        request = self.env['approval.request'].browse()
        document = False
        approval_type = self.env.ref('approvals.mail_activity_data_approval', raise_if_not_found=False)
        output_type = self.env.ref('entintados_pdv.mail_activity_type_material_output', raise_if_not_found=False)
        if self.res_model == 'approval.request' and approval_type and self.activity_type_id == approval_type:
            request = self.env['approval.request'].sudo().browse(self.res_id).exists()
            category = self.env.ref('entintados_pdv.approval_category_salida_material', raise_if_not_found=False)
            if request and category and request.category_id == category and self.user_id in request.approver_ids.user_id:
                document, stage = request, 'aprobación'
        elif self.res_model == 'stock.picking' and output_type and self.activity_type_id == output_type:
            picking = self.env['stock.picking'].sudo().browse(self.res_id).exists()
            if picking and picking.is_crm_demo_output:
                request = picking.approval_request_id
                if self.user_id in request.material_auditor_ids.user_id:
                    document, stage = picking, 'validación'
        lead = request.crm_lead_id
        if not stage or not lead:
            return {}
        partner = document.partner_id or lead.partner_id
        return {
            'stage': stage, 'folio': document.name or '',
            'salesperson': lead.user_id.name or 'Sin vendedor asignado',
            'contact': partner.name or lead.display_name,
            'email_from': document.company_id.partner_id.email_formatted,
        }

    @api.model_create_multi
    def create(self, vals_list):
        activities = super().create(vals_list)
        template = self.env.ref('entintados_pdv.mail_template_demo_activity', raise_if_not_found=False)
        if template:
            for activity in activities:
                if activity.user_id.partner_id.email and activity._demo_notification_values():
                    # Renderizar y poner en cola, sin bloquear la creación con SMTP.
                    # La cola se confirma en la misma transacción que la actividad.
                    template.sudo().send_mail(
                        activity.id, force_send=False,
                        email_values={'recipient_ids': [Command.set(activity.user_id.partner_id.ids)]})
        return activities
