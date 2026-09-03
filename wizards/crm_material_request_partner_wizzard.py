from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CrmMaterialRequestPartnerWizzard(models.TransientModel):
    _name = 'crm.material.request.partner.wizzard'
    _description = 'Asignar contacto antes de solicitar salida de material'

    lead_id = fields.Many2one('crm.lead', required=True, ondelete='cascade')
    action_type = fields.Selection([
        ('create', 'Crear un nuevo cliente'),
        ('existing', 'Vincular a un cliente existente'),
    ], string='Contacto', default='create', required=True)
    partner_name = fields.Char(string='Nombre del nuevo cliente')
    partner_phone = fields.Char(string='Teléfono del nuevo cliente')
    partner_id = fields.Many2one('res.partner', string='Cliente existente')

    @api.onchange('lead_id')
    def _onchange_lead_id(self):
        if self.lead_id:
            self.partner_name = (
                self.lead_id.partner_name
                or self.lead_id.contact_name
                or self.lead_id.name
            )

    def action_confirm(self):
        self.ensure_one()

        if self.action_type == 'create':
            if not self.partner_name:
                raise UserError(_("Ingresa el nombre del nuevo cliente."))
            if not self.partner_phone:
                raise UserError(_("Ingresa el teléfono del nuevo cliente."))
            partner = self.env['res.partner'].create({
                'name': self.partner_name,
                'email': self.lead_id.email_from,
                'phone': self.partner_phone,
            })
            self.lead_id.partner_id = partner.id
        else:
            if not self.partner_id:
                raise UserError(_("Selecciona un cliente existente."))
            self.lead_id.partner_id = self.partner_id.id

        # Vuelve a llamar el método original: ahora sí tiene partner_id
        return self.lead_id.action_request_material_output()