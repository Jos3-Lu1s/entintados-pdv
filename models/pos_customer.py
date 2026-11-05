from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResPartnerPosContactInfo(models.Model):
    _inherit = 'res.partner'

    pos_contact_info = fields.Json(compute='_compute_pos_contact_info', compute_sudo=False)

    def _pos_contact_address(self):
        self.ensure_one()
        return ', '.join(filter(None, [self.street, self.street2,
            ' '.join(filter(None, [self.zip, self.city])), self.state_id.name, self.country_id.name]))

    @api.depends('name', 'ref', 'vat', 'street', 'street2', 'zip', 'city', 'state_id.name',
                 'country_id.name', 'sale_owner_name', 'l10n_mx_edi_fiscal_regime',
                 'commercial_partner_id.ref', 'commercial_partner_id.vat',
                 'commercial_partner_id.l10n_mx_edi_fiscal_regime', 'child_ids',
                 'child_ids.type', 'child_ids.name', 'child_ids.street', 'child_ids.street2',
                 'child_ids.zip', 'child_ids.city', 'child_ids.state_id.name', 'child_ids.country_id.name')
    @api.depends_context('lang')
    def _compute_pos_contact_info(self):
        regimes = dict(self._fields['l10n_mx_edi_fiscal_regime']._description_selection(self.env))
        for partner in self:
            commercial = partner.commercial_partner_id
            addresses = partner.address_get(['invoice', 'delivery'])
            invoice = self.browse(addresses.get('invoice'))
            delivery = self.browse(addresses.get('delivery'))
            partner.pos_contact_info = {
                'reference': partner.ref or commercial.ref or '',
                'vat': partner.vat or commercial.vat or '',
                'salesperson': partner.sale_owner_name or '',
                'address': partner._pos_contact_address(),
                'fiscal_regime': regimes.get(partner.l10n_mx_edi_fiscal_regime or commercial.l10n_mx_edi_fiscal_regime, ''),
                'invoice': {'name': invoice.name or '', 'address': invoice._pos_contact_address() if invoice else ''},
                'delivery': {'name': delivery.name or '', 'address': delivery._pos_contact_address() if delivery else ''},
            }

    @api.model
    def _load_pos_data_fields(self, config):
        return list(dict.fromkeys(super()._load_pos_data_fields(config) + ['pos_contact_info']))


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _check_pos_customer(self):
        if any(not order.partner_id for order in self):
            raise UserError(_('Selecciona primero un cliente para iniciar la compra.'))

    def _process_saved_order(self, draft):
        if self.lines:
            self._check_pos_customer()
        return super()._process_saved_order(draft)

    def action_pos_order_paid(self):
        self._check_pos_customer()
        return super().action_pos_order_paid()
